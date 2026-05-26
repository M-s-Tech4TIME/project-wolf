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

from app.models.interface import ModelProvider

logger = structlog.get_logger(__name__)


# Split on sentence-final punctuation followed by whitespace and a
# capital/digit start. The letter-before-period requirement avoids
# treating numbered-list markers ("1.", "2.") as sentence ends:
#   "1. Run list_agents." stays one claim, not three.
# Imperfect but works on technical English declarative answers.
_SENTENCE_SPLIT = re.compile(r"(?<=[a-zA-Z][.!?])\s+(?=[A-Z\d])")


@dataclass(frozen=True)
class ClaimVerdict:
    """One claim and its verdict."""

    claim: str
    # 'supported' | 'unsupported' | 'unverifiable'
    # supported: claim maps to at least one evidence segment.
    # unsupported: judge found no support in the evidence.
    # unverifiable: judge couldn't decide (used for trivial / opinion /
    #               instruction-following sentences like "Here is what I found:").
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
    unverifiable_count: int = 0


VALIDATOR_SYSTEM_PROMPT = (
    "You are a strict fact-checker. You will be given EVIDENCE (the only "
    "facts you may rely on) and a list of CLAIMS extracted from an "
    "assistant's draft answer.\n\n"
    "For each claim, judge it as one of:\n"
    "  - SUPPORTED:    a clear, specific evidence segment supports the claim.\n"
    "  - UNSUPPORTED:  the claim makes a specific factual assertion not "
    "found in the evidence.\n"
    "  - UNVERIFIABLE: the claim is a transition / opinion / preamble / "
    "instruction (e.g. 'Here is what I found:') — no factual content to "
    "verify.\n\n"
    "Be strict: combining two real facts into a third inferred fact is "
    "UNSUPPORTED unless the inference itself is in the evidence.\n\n"
    "Output ONLY a JSON array of objects with keys: 'index' (int), "
    "'verdict' (one of 'supported' / 'unsupported' / 'unverifiable'), "
    "'reason' (one short sentence). No prose outside the JSON array."
)


class GroundingValidator:
    """LLM-as-judge validator. Pluggable model provider so tests can stub it."""

    def __init__(self, provider: ModelProvider, *, max_claims: int = 20) -> None:
        self._provider = provider
        # Safety cap — extremely long answers might be the model in a
        # runaway state. Cap claim count to keep the judge call bounded.
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
    ) -> str:
        """Concatenate every evidence source the agent saw, with cheap
        provenance tags so the judge can cite which source supported what.

        `tool_results` is the raw dispatch results list from the loop;
        `retrieved_chunks` is whatever query_runbook returned. Both are
        passed as dicts (not Pydantic models) to keep this module free
        of agent-loop imports.
        """
        sections: list[str] = []
        for i, tr in enumerate(tool_results, start=1):
            name = tr.get("name", "<unknown>")
            content = tr.get("content")
            if not content:
                continue
            sections.append(
                f"[TOOL_RESULT {i}: {name}]\n{json.dumps(content, default=str)[:2000]}"
            )
        for i, chunk in enumerate(retrieved_chunks, start=1):
            title = chunk.get("chunk_metadata", {}).get("title", "<no title>")
            source_type = chunk.get("source_type", "<unknown>")
            content = chunk.get("content", "")
            sections.append(
                f"[KNOWLEDGE {i}: {source_type} — {title}]\n{content[:2000]}"
            )
        return "\n\n".join(sections)

    async def validate(
        self,
        answer: str,
        *,
        tool_results: list[dict[str, Any]],
        retrieved_chunks: list[dict[str, Any]],
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

        evidence = self.build_evidence(tool_results, retrieved_chunks)
        if not evidence.strip():
            # Nothing to verify against; skip rather than over-flag.
            logger.info("grounding_skipped", reason="no_evidence", loop_id=loop_id)
            return result

        try:
            verdicts = await self._judge(evidence, claims)
        except Exception as exc:
            logger.warning(
                "grounding_judge_failed",
                error_type=type(exc).__name__,
                error_msg=str(exc) or "(no message)",
                loop_id=loop_id,
            )
            return result

        # Map verdicts back to claims by index; default to unverifiable
        # for claims the judge skipped.
        per_claim: dict[int, ClaimVerdict] = {}
        for v in verdicts:
            idx = v.get("index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(claims):
                continue
            verdict = str(v.get("verdict", "unverifiable")).lower()
            if verdict not in {"supported", "unsupported", "unverifiable"}:
                verdict = "unverifiable"
            per_claim[idx] = ClaimVerdict(
                claim=claims[idx],
                verdict=verdict,
                reason=str(v.get("reason", ""))[:200],
            )

        for i, claim in enumerate(claims):
            verdict = per_claim.get(
                i, ClaimVerdict(claim=claim, verdict="unverifiable", reason="")
            )
            result.claims.append(verdict)
            if verdict.verdict == "supported":
                result.supported_count += 1
            elif verdict.verdict == "unsupported":
                result.unsupported_count += 1
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
        # Tolerate the common "```json …```" wrapping some models emit.
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
        # Extract the first JSON array; some small models append explanation.
        match = re.search(r"\[\s*\{.*?\}\s*\]", text, flags=re.DOTALL)
        if match:
            text = match.group(0)
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError(f"Expected JSON array, got {type(parsed).__name__}")
        return parsed

    @staticmethod
    def _annotate(answer: str, verdicts: list[ClaimVerdict]) -> str:
        """Insert `[unverified]` markers after each unsupported claim.

        Conservative implementation: find the claim's text in the original
        answer and splice the marker. If a claim's text doesn't appear
        verbatim (sentence splitter normalized whitespace or lost a
        trailing punctuation mark), skip the marker for that claim rather
        than guess. Avoid producing a misleading-but-confident annotation.
        """
        annotated = answer
        for v in verdicts:
            if v.verdict != "unsupported":
                continue
            # Locate the original claim text; tolerate one trailing
            # punctuation char that the splitter consumed.
            for candidate in (v.claim, v.claim + ".", v.claim + "!", v.claim + "?"):
                idx = annotated.find(candidate)
                if idx >= 0:
                    insertion_point = idx + len(candidate)
                    annotated = (
                        annotated[:insertion_point]
                        + " [unverified]"
                        + annotated[insertion_point:]
                    )
                    break
        return annotated
