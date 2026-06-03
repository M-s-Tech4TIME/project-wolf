"""LLM-as-judge grounding validator.

The validator splits the agent's draft answer into claims (sentence-level),
collects all evidence (tool result contents + retrieved knowledge chunk
contents), and asks a model to label each claim as supported or unsupported.
Unsupported claims are then marked `[unverified]` inline in the final
answer text.

Design notes:

  - **LLM-as-judge over heuristic.** Token-overlap was considered and
    rejected: the canonical embellishment we want to catch ("Block the
    source IP for 60 seconds per `ignore` parameter" stitched from a
    rule's alert-suppression `ignore=60s` parameter into a runbook step
    that doesn't exist) shares tokens with the evidence but combines
    them in a way that isn't actually supported. Only a semantic judge
    catches this reliably. Trade-off: one extra model call per
    knowledge-flavored answer.

  - **No nested validation.** The validator does not re-validate its own
    output (recursion risk). Its output is structured JSON; a malformed
    response degrades gracefully to "validation skipped, original
    answer returned."

  - **Failure modes are non-blocking.** If the judge model call fails or
    returns malformed output, the agent loop logs the error and returns
    the original answer un-annotated. Phase 3's grounding posture is
    "flag what we can verify, do not silently drop content," matching
    doc 06's recommended behaviour and the operator's Slice 2 choice
    (mark inline as `[unverified]`).
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

import structlog
from wolf_schema import ChatRequest
from wolf_schema.chat import Message, MessageRole

from wolf_server.models.interface import ModelProvider

logger = structlog.get_logger(__name__)


# Split on sentence-final punctuation followed by whitespace and a
# capital/digit start. The letter-before-period requirement avoids
# treating numbered-list markers ("1.", "2.") as sentence ends:
#   "1. Run list_agents." stays one claim, not three.
# Imperfect but works on technical English declarative answers.
_SENTENCE_SPLIT = re.compile(r"(?<=[a-zA-Z][.!?])\s+(?=[A-Z\d])")

# Four inline markers, one per verdict (Slice 5.0c-a). The frontend
# renders each as a distinct chip so the analyst sees a signal for every
# claim, not only the worrying ones.
#   [verified]    — green: directly backed by tool result / knowledge chunk.
#   [unverified]  — yellow ("Uncertain"): factual but evidence neither confirms
#                   nor contradicts (general knowledge, inference).
#   [unsupported] — red ("Not Verified"): contradicts evidence or fabricates
#                   specifics (counts, IDs, names) absent from it.
#   [non-factual] — muted yellow: no factual content to check (preamble,
#                   transition, opinion, instruction).
MARKER_VERIFIED = "[verified]"
MARKER_UNVERIFIED = "[unverified]"
MARKER_UNSUPPORTED = "[unsupported]"
MARKER_NON_FACTUAL = "[non-factual]"

_VALID_VERDICTS = {"supported", "unsupported", "uncertain", "unverifiable"}
# Every verdict now gets a marker — full visibility per user request.
_VERDICT_MARKER = {
    "supported": MARKER_VERIFIED,
    "uncertain": MARKER_UNVERIFIED,
    "unsupported": MARKER_UNSUPPORTED,
    "unverifiable": MARKER_NON_FACTUAL,
}

# Cap on characters per evidence source fed to the judge (Slice 5.0b.1).
# 2 KB was tight enough that rule descriptions on a multi-hit search_alerts
# JSON could be truncated out of view, leading to the judge marking
# paraphrases of those descriptions as "unsupported" because it literally
# couldn't see them. A 5-hit search_alerts result is roughly 1.5 KB of
# JSON; we size to comfortably fit ~12 hits (the practical worst case for
# a single tool call) plus the wrapper. qwen3:8b's default context window
# (32K tokens) has ample headroom even with several sources at this cap.
_EVIDENCE_PER_SOURCE_LIMIT = 5000


@dataclass(frozen=True)
class ClaimVerdict:
    """One claim and its verdict.

    Verdicts (Slice 5.0b — four levels so the UI can show caution vs error):
      supported    — a specific evidence segment backs the claim. No marker.
      unsupported  — a specific fact (count/ID/name/timestamp/status) that
                     contradicts the evidence or should have come from it but
                     is absent. Fabrications land here. → red marker.
      uncertain    — a plausible general statement / best-practice / inference
                     the evidence neither confirms nor contradicts. → yellow.
      unverifiable — no factual content (transition / preamble / opinion /
                     instruction, e.g. "Here is what I found:"). No marker.
    """

    claim: str
    verdict: str
    reason: str = ""


@dataclass
class ValidationResult:
    """Aggregate verdict for the answer."""

    claims: list[ClaimVerdict] = field(default_factory=list)
    # Original answer with `[unverified]` markers inserted on unsupported
    # claims. If validation failed (no judge call possible), this equals
    # the original answer unchanged.
    annotated_answer: str = ""
    # True if the validator ran successfully (regardless of how many
    # claims were marked). False if the judge call failed or returned
    # malformed output — caller can use this to surface "validation
    # skipped" to the user.
    ran: bool = False
    # Counts for the audit trail and the LoopEvent.
    supported_count: int = 0
    unsupported_count: int = 0
    uncertain_count: int = 0
    unverifiable_count: int = 0


VALIDATOR_SYSTEM_PROMPT = (
    "You are a strict fact-checker. You will be given EVIDENCE (the only "
    "facts you may rely on) and a list of CLAIMS extracted from an "
    "assistant's draft answer.\n\n"
    "Judge each claim as exactly one of:\n"
    "  - SUPPORTED:    a specific evidence segment clearly backs the claim, "
    "OR the claim paraphrases / generalises content that is clearly in the "
    "evidence (e.g. claim 'this is a brute-force pattern' is SUPPORTED when "
    "a rule description literally says 'brute force trying to get access').\n"
    "  - UNSUPPORTED:  the claim states a specific fact (a number, count, ID, "
    "name, timestamp, or status) that CONTRADICTS the evidence, or that "
    "should have come from the evidence but is absent. Fabricated specifics "
    "go here.\n"
    "  - UNCERTAIN:    a plausible general statement, best-practice, or "
    "reasonable inference the evidence neither confirms nor contradicts — "
    "you cannot verify it, but it is not obviously wrong.\n"
    "  - UNVERIFIABLE: the claim has no factual content to check. Examples:\n"
    "      * transitions / preambles ('Here is what I found:', 'Let me "
    "summarise:')\n"
    "      * meta commentary about your own analysis flow ('No further tool "
    "calls are needed', 'I have what I need to answer', 'The data is "
    "sufficient', 'No additional function calls are required')\n"
    "      * pure opinions / instructions ('You should investigate this', "
    "'Block the IP immediately')\n\n"
    "Rules:\n"
    "  - Any specific number / count / ID / name / timestamp NOT present in "
    "the evidence is UNSUPPORTED, never UNCERTAIN.\n"
    "  - Combining two real facts into a third inferred fact is UNSUPPORTED "
    "unless the inference itself appears in the evidence.\n"
    "  - If the EVIDENCE shows a tool FAILED and returned no data, any "
    "specific factual claim that needed that tool is UNSUPPORTED.\n"
    "  - A paraphrase of evidence is SUPPORTED, not UNSUPPORTED. Only mark "
    "it UNSUPPORTED if the paraphrase contradicts the evidence or adds "
    "specifics that aren't in it.\n\n"
    "Output ONLY a JSON array of objects with keys: 'index' (int), "
    "'verdict' (one of 'supported' / 'unsupported' / 'uncertain' / "
    "'unverifiable'), 'reason' (one short sentence). No prose outside the "
    "JSON array."
)


class GroundingValidator:
    """LLM-as-judge validator. Pluggable model provider so tests can stub it."""

    def __init__(self, provider: ModelProvider, *, max_claims: int = 12) -> None:
        self._provider = provider
        # Safety cap — extremely long answers might be the model in a
        # runaway state. Also keeps the judge's input small enough that
        # qwen3:8b's Ollama context doesn't overflow (Slice 5.0b.4 lowered
        # this from 20 to 12 after empty-output failures on long
        # structured answers with many bullets/table rows).
        self._max_claims = max_claims

    @staticmethod
    def split_claims(answer: str) -> list[str]:
        """Sentence-level claim extraction.

        Bullet / numbered list items are addressed as separate claims.
        Returns the textual claims in order; the caller maps verdicts back
        to text positions for inline annotation.
        """
        if not answer.strip():
            return []
        parts = _SENTENCE_SPLIT.split(answer.strip())
        # Strip + drop empties; preserve leading bullet/number characters
        # so the inline annotator can splice the [unverified] marker
        # before the trailing punctuation cleanly.
        return [p.strip() for p in parts if p.strip()]

    @staticmethod
    def build_evidence(
        tool_results: list[dict[str, Any]],
        retrieved_chunks: list[dict[str, Any]],
        tool_failures: list[dict[str, Any]] | None = None,
    ) -> str:
        """Concatenate every evidence source the agent saw, with cheap
        provenance tags so the judge can cite which source supported what.

        `tool_results` is the raw dispatch results list from the loop;
        `retrieved_chunks` is whatever query_runbook returned. Both are
        passed as dicts (not Pydantic models) to keep this module free
        of agent-loop imports.

        `tool_failures` (Slice 5.0b) are tool calls that errored and produced
        NO data. They are surfaced as explicit negative evidence so the judge
        flags specific claims that should have come from a failed tool — this
        is what catches "the tool errored, so the model fabricated numbers."
        """
        sections: list[str] = []
        for i, tr in enumerate(tool_results, start=1):
            name = tr.get("name", "<unknown>")
            content = tr.get("content")
            if not content:
                continue
            body = json.dumps(content, default=str)[:_EVIDENCE_PER_SOURCE_LIMIT]
            sections.append(f"[TOOL_RESULT {i}: {name}]\n{body}")
        for i, chunk in enumerate(retrieved_chunks, start=1):
            title = chunk.get("chunk_metadata", {}).get("title", "<no title>")
            source_type = chunk.get("source_type", "<unknown>")
            content = chunk.get("content", "")
            sections.append(
                f"[KNOWLEDGE {i}: {source_type} — {title}]\n"
                f"{content[:_EVIDENCE_PER_SOURCE_LIMIT]}"
            )
        for i, tf in enumerate(tool_failures or [], start=1):
            name = tf.get("name", "<unknown>")
            error = str(tf.get("error", "tool call failed"))[:300]
            sections.append(
                f"[TOOL_FAILED {i}: {name}]\nThis tool returned NO data "
                f"(error: {error}). Any specific fact that would have come "
                f"from it is UNSUPPORTED."
            )
        return "\n\n".join(sections)

    async def validate(
        self,
        answer: str,
        *,
        tool_results: list[dict[str, Any]],
        retrieved_chunks: list[dict[str, Any]],
        tool_failures: list[dict[str, Any]] | None = None,
        loop_id: str = "",
    ) -> ValidationResult:
        """Run the validator on a draft answer. Always returns a result;
        failures degrade to `ran=False` with `annotated_answer=answer`."""
        result = ValidationResult(annotated_answer=answer)

        claims = self.split_claims(answer)
        if not claims:
            logger.info("grounding_skipped", reason="empty_answer", loop_id=loop_id)
            return result
        if len(claims) > self._max_claims:
            logger.info(
                "grounding_truncated",
                claim_count=len(claims),
                max_claims=self._max_claims,
                loop_id=loop_id,
            )
            claims = claims[: self._max_claims]

        evidence = self.build_evidence(tool_results, retrieved_chunks, tool_failures)
        if not evidence.strip():
            # Nothing to verify against; skip rather than over-flag.
            logger.info("grounding_skipped", reason="no_evidence", loop_id=loop_id)
            return result

        # Try the judge up to twice. The first call can fail (ReadTimeout
        # on a cold-loading judge model, transient Ollama errors); the
        # retry usually lands on a now-warm model. Same persistence
        # philosophy as the partial-response retry below. Slice 5.0b.3.
        verdicts: list[dict[str, Any]] | None = None
        for attempt in (0, 1):
            try:
                verdicts = await self._judge(evidence, claims)
                break
            except Exception as exc:
                level_logger = logger.info if attempt == 0 else logger.warning
                level_logger(
                    "grounding_judge_failed" if attempt == 1
                    else "grounding_judge_first_attempt_failed",
                    error_type=type(exc).__name__,
                    error_msg=str(exc) or "(no message)",
                    attempt=attempt + 1,
                    loop_id=loop_id,
                )
        if verdicts is None:
            # Both attempts failed — return the un-annotated answer rather
            # than fake verdicts. The caller surfaces this as ran=False.
            return result

        # Map verdicts back to claims by index.
        per_claim: dict[int, ClaimVerdict] = {}
        self._merge_verdicts(verdicts, claims, per_claim)

        # qwen3:8b occasionally returns a partial JSON array (fewer verdicts
        # than claims). When that happens, retry the judge call once — its
        # output is non-deterministic enough at temp=0 that a second call
        # often covers the missing claims (Slice 5.0b.2). After the retry,
        # any STILL-missing claim is filled with `uncertain` (yellow) rather
        # than `unverifiable` (silent / no chip) — the user should always
        # see a signal when the validator couldn't classify something.
        missing = [i for i in range(len(claims)) if i not in per_claim]
        if missing:
            logger.info(
                "grounding_judge_partial_response",
                loop_id=loop_id,
                missing=len(missing),
                total=len(claims),
            )
            try:
                retry_verdicts = await self._judge(evidence, claims)
            except Exception as exc:
                logger.warning(
                    "grounding_judge_retry_failed",
                    loop_id=loop_id,
                    error_type=type(exc).__name__,
                )
            else:
                self._merge_verdicts(retry_verdicts, claims, per_claim)
        for i in range(len(claims)):
            if i not in per_claim:
                per_claim[i] = ClaimVerdict(
                    claim=claims[i],
                    verdict="uncertain",
                    reason="judge returned no verdict for this claim",
                )

        for i in range(len(claims)):
            verdict = per_claim[i]
            result.claims.append(verdict)
            if verdict.verdict == "supported":
                result.supported_count += 1
            elif verdict.verdict == "unsupported":
                result.unsupported_count += 1
            elif verdict.verdict == "uncertain":
                result.uncertain_count += 1
            else:
                result.unverifiable_count += 1

        result.annotated_answer = self._annotate(answer, result.claims)
        result.ran = True
        logger.info(
            "grounding_completed",
            loop_id=loop_id,
            claims=len(result.claims),
            supported=result.supported_count,
            unsupported=result.unsupported_count,
            uncertain=result.uncertain_count,
            unverifiable=result.unverifiable_count,
        )
        return result

    async def _judge(self, evidence: str, claims: list[str]) -> list[dict[str, Any]]:
        numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(claims))
        user = (
            f"EVIDENCE:\n{evidence}\n\n"
            f"CLAIMS:\n{numbered}\n\n"
            "Respond with a JSON array of objects: "
            '[{"index": 0, "verdict": "supported|unsupported|unverifiable", '
            '"reason": "..."}, ...]'
        )
        request = ChatRequest(
            messages=[
                Message(role=MessageRole.system, content=VALIDATOR_SYSTEM_PROMPT),
                Message(role=MessageRole.user, content=user),
            ],
        )
        response = await self._provider.chat(request)
        text = (response.content or "").strip()
        if not text:
            # Explicit signal — distinguishes "model returned nothing" (the
            # common context-overflow failure on a constrained judge) from
            # a malformed-JSON parse error downstream. Slice 5.0b.4.
            raise ValueError("judge model returned empty content")
        # Tolerate the common "```json …```" wrapping some models emit.
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
        # Strip qwen3 / R1-style <think>…</think> reasoning blocks before
        # looking for the JSON array.
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        # Extract the first JSON array; some small models append explanation.
        match = re.search(r"\[\s*\{.*?\}\s*\]", text, flags=re.DOTALL)
        if match:
            text = match.group(0)
        if not text.strip():
            raise ValueError("judge response contained no JSON array")
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError(f"Expected JSON array, got {type(parsed).__name__}")
        return parsed

    @staticmethod
    def _merge_verdicts(
        raw: list[dict[str, Any]],
        claims: list[str],
        into: dict[int, ClaimVerdict],
    ) -> None:
        """Map raw judge-response verdicts onto claim indices, additively.

        Skips out-of-range indices and unknown verdict strings (falls back
        to `unverifiable`). The first valid verdict for each index wins —
        a later retry merge cannot overwrite the original judgement, only
        fill in indices the first call missed.
        """
        for v in raw:
            idx = v.get("index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(claims):
                continue
            if idx in into:
                continue  # don't overwrite a verdict from an earlier call
            verdict = str(v.get("verdict", "unverifiable")).lower()
            if verdict not in _VALID_VERDICTS:
                verdict = "unverifiable"
            into[idx] = ClaimVerdict(
                claim=claims[idx],
                verdict=verdict,
                reason=str(v.get("reason", ""))[:200],
            )

    @staticmethod
    def _annotate(answer: str, verdicts: list[ClaimVerdict]) -> str:
        """Insert severity markers after flagged claims.

        `uncertain` claims get the yellow `[unverified]` marker; `unsupported`
        claims get the red `[unsupported]` marker. `supported` and
        `unverifiable` claims get nothing.

        Conservative implementation: find the claim's text in the original
        answer and splice the marker. If a claim's text doesn't appear
        verbatim (sentence splitter normalized whitespace or lost a
        trailing punctuation mark), skip the marker for that claim rather
        than guess. Avoid producing a misleading-but-confident annotation.
        """
        annotated = answer
        for v in verdicts:
            marker = _VERDICT_MARKER.get(v.verdict)
            if marker is None:
                continue
            # Locate the original claim text; tolerate one trailing
            # punctuation char that the splitter consumed.
            for candidate in (v.claim, v.claim + ".", v.claim + "!", v.claim + "?"):
                idx = annotated.find(candidate)
                if idx >= 0:
                    insertion_point = idx + len(candidate)
                    annotated = (
                        annotated[:insertion_point]
                        + f" {marker}"
                        + annotated[insertion_point:]
                    )
                    break
        return annotated
