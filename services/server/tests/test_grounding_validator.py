"""Tests for the Phase 3 Slice 2B grounding validator.

The validator is the structural mitigation for the cross-model
grounding-discipline failures recorded in ADRs 0002 / 0010 / 0011 and
the synthesis-embellishment observed during Slice 1's mixed-mode test.
Tests focus on the public contract: claim splitting, annotation,
graceful degradation, and verdict counting. The LLM judge is stubbed
so tests don't require a live model.
"""

import json
from typing import Any

import pytest
from wolf_schema import ChatRequest, ChatResponse
from wolf_server.grounding import GroundingValidator, ValidationResult


class _StubProvider:
    """Minimal ModelProvider stub. Returns whatever JSON judgment string
    the test wires up via `set_response`."""

    def __init__(self, response_content: str = "[]") -> None:
        self._content = response_content
        self.last_request: ChatRequest | None = None

    def capability(self) -> Any:
        # Validator never reads .capability(); included for completeness.
        class _C:
            model_id = "stub"
            provider = "stub"

        return _C()

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.last_request = request
        return ChatResponse(
            content=self._content,
            tool_calls=[],
            input_tokens=0,
            output_tokens=0,
            stop_reason="stop",
            model_id="stub",
        )


# ─── split_claims ────────────────────────────────────────────────────────────


def test_split_claims_handles_simple_sentences() -> None:
    out = GroundingValidator.split_claims(
        "The agent is active. Rule 5712 fires on SSH failures. Block the IP."
    )
    assert out == [
        "The agent is active.",
        "Rule 5712 fires on SSH failures.",
        "Block the IP.",
    ]


def test_split_claims_handles_numbered_list() -> None:
    out = GroundingValidator.split_claims(
        "Steps:\n1. Run list_agents.\n2. Check the IP.\n3. Block at perimeter."
    )
    # The splitter recognises bullet markers on a new line as claim boundaries.
    assert any("1. Run list_agents." in s for s in out)
    assert any("2. Check the IP." in s for s in out)
    assert any("3. Block at perimeter." in s for s in out)


def test_split_claims_on_empty_string() -> None:
    assert GroundingValidator.split_claims("") == []
    assert GroundingValidator.split_claims("   \n  ") == []


# ─── build_evidence ──────────────────────────────────────────────────────────


def test_build_evidence_tags_tool_results_and_knowledge_separately() -> None:
    evidence = GroundingValidator.build_evidence(
        tool_results=[{"name": "get_rule_definition", "content": {"id": 5712}}],
        retrieved_chunks=[
            {
                "source_type": "runbook",
                "chunk_metadata": {"title": "ACME runbook"},
                "content": "Step 1: verify reachability.",
            }
        ],
    )
    assert "TOOL_RESULT 1: get_rule_definition" in evidence
    assert "KNOWLEDGE 1: runbook" in evidence
    assert "ACME runbook" in evidence
    assert "Step 1: verify reachability." in evidence


def test_build_evidence_empty_inputs() -> None:
    assert GroundingValidator.build_evidence([], []) == ""


# ─── validate() — happy paths ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_all_supported_leaves_answer_unchanged() -> None:
    provider = _StubProvider(
        json.dumps(
            [
                {"index": 0, "verdict": "supported", "reason": "in evidence"},
                {"index": 1, "verdict": "supported", "reason": "in evidence"},
            ]
        )
    )
    v = GroundingValidator(provider)
    answer = "Rule 5712 fires on 8 failures. The threshold is 120 seconds."
    result = await v.validate(
        answer,
        tool_results=[{"name": "get_rule_definition", "content": {"id": 5712}}],
        retrieved_chunks=[],
    )
    assert result.ran is True
    assert result.supported_count == 2
    assert result.unsupported_count == 0
    # No yellow / red markers, but supported claims now get [verified]
    # chips inline (Slice 5.0c-a — every verdict gets a marker).
    assert "[unverified]" not in result.annotated_answer
    assert "[unsupported]" not in result.annotated_answer
    assert "[verified]" in result.annotated_answer


@pytest.mark.asyncio
async def test_validate_marks_unsupported_claim_inline() -> None:
    """Canonical test: the embellishment we saw in Slice 1's mixed-mode."""
    provider = _StubProvider(
        json.dumps(
            [
                {"index": 0, "verdict": "supported", "reason": "in evidence"},
                {"index": 1, "verdict": "unsupported", "reason": "not in evidence"},
            ]
        )
    )
    v = GroundingValidator(provider)
    answer = (
        "Rule 5712 fires on 8 SSH failures within 120 seconds. "
        "Block the source IP for 60 seconds per the ignore parameter."
    )
    result = await v.validate(
        answer,
        tool_results=[{"name": "get_rule_definition", "content": {"id": 5712}}],
        retrieved_chunks=[
            {
                "source_type": "runbook",
                "chunk_metadata": {"title": "ACME"},
                "content": "Step 1: verify agent. Step 2: block at perimeter for external IPs.",
            }
        ],
    )
    assert result.ran is True
    assert result.supported_count == 1
    assert result.unsupported_count == 1
    # unsupported → RED [unsupported] marker (Slice 5.0b).
    assert "[unsupported]" in result.annotated_answer
    assert "[unverified]" not in result.annotated_answer
    # And only the suspect sentence gets it.
    supported_idx = result.annotated_answer.find("Rule 5712 fires on 8 SSH failures")
    suspect_idx = result.annotated_answer.find("Block the source IP")
    marker_idx = result.annotated_answer.find("[unsupported]")
    assert marker_idx > suspect_idx > supported_idx


@pytest.mark.asyncio
async def test_validate_marks_uncertain_claim_yellow() -> None:
    """uncertain → yellow [unverified] marker, NOT red (Slice 5.0b)."""
    provider = _StubProvider(
        json.dumps(
            [
                {"index": 0, "verdict": "supported", "reason": "in evidence"},
                {"index": 1, "verdict": "uncertain", "reason": "general knowledge"},
            ]
        )
    )
    v = GroundingValidator(provider)
    answer = "Rule 5712 fires on 8 SSH failures. Brute-force attacks are a common attack vector."
    result = await v.validate(
        answer,
        tool_results=[{"name": "get_rule_definition", "content": {"id": 5712}}],
        retrieved_chunks=[],
    )
    assert result.ran is True
    assert result.uncertain_count == 1
    assert result.unsupported_count == 0
    assert "[unverified]" in result.annotated_answer
    assert "[unsupported]" not in result.annotated_answer


@pytest.mark.asyncio
async def test_failed_tool_makes_evidence_and_flags_fabrication() -> None:
    """A failed tool is negative evidence; the validator still runs and a
    fabricated specific is judged unsupported (Slice 5.0b hardening)."""
    provider = _StubProvider(
        json.dumps(
            [
                {"index": 0, "verdict": "unsupported", "reason": "tool failed, no data"},
            ]
        )
    )
    v = GroundingValidator(provider)
    result = await v.validate(
        "There were 1,478 alerts in the last six months.",
        tool_results=[],
        retrieved_chunks=[],
        tool_failures=[{"name": "count_alerts_by_severity", "error": "input too short"}],
    )
    # Evidence was non-empty (the failure note), so the judge ran.
    assert provider.last_request is not None
    assert result.ran is True
    assert result.unsupported_count == 1
    assert "[unsupported]" in result.annotated_answer


def test_build_evidence_includes_failure_notes() -> None:
    ev = GroundingValidator.build_evidence(
        tool_results=[],
        retrieved_chunks=[],
        tool_failures=[{"name": "count_alerts_by_severity", "error": "boom"}],
    )
    assert "TOOL_FAILED" in ev
    assert "count_alerts_by_severity" in ev
    assert "boom" in ev


def test_build_evidence_per_source_limit_fits_multi_hit_results() -> None:
    """Slice 5.0b.1: cap sized to fit a realistic multi-hit search_alerts
    JSON (≈12 hits × ~350 chars) without truncating later hits out of
    the judge's view. Previous 2 KB was the truncation cause behind
    over-strict 'unsupported' verdicts on real answers."""
    big_blob = "X" * 8000
    ev = GroundingValidator.build_evidence(
        tool_results=[{"name": "search_alerts", "content": big_blob}],
        retrieved_chunks=[],
    )
    # ~5000 X's survive (json.dumps adds a leading quote so the run lands
    # at cap - 1); the previous 2 KB and 3 KB caps are no longer in force.
    x_run = ev.count("X")
    assert 4950 <= x_run <= 5000, x_run
    assert x_run > 3500


class _ScriptedProvider:
    """Stub that returns a different canned response on each successive call."""

    def __init__(self, scripted_contents: list[str]) -> None:
        self._scripted = list(scripted_contents)
        self.call_count = 0
        self.last_request: ChatRequest | None = None

    def capability(self) -> Any:
        class _C:
            model_id = "stub"
            provider = "stub"

        return _C()

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.last_request = request
        idx = min(self.call_count, len(self._scripted) - 1)
        content = self._scripted[idx]
        self.call_count += 1
        return ChatResponse(
            content=content,
            tool_calls=[],
            input_tokens=0,
            output_tokens=0,
            stop_reason="stop",
            model_id="stub",
        )


@pytest.mark.asyncio
async def test_partial_judge_response_retries_and_recovers() -> None:
    """qwen3:8b sometimes returns N-1 verdicts for N claims.

    The validator retries the judge once; the second call covers the
    missing claim. No claim should default to uncertain when the retry
    succeeds. (Slice 5.0b.2)
    """
    first_partial = json.dumps(
        [
            {"index": 1, "verdict": "supported", "reason": "in evidence"},
        ]
    )
    second_covers_missing = json.dumps(
        [
            {"index": 0, "verdict": "supported", "reason": "in evidence too"},
        ]
    )
    provider = _ScriptedProvider([first_partial, second_covers_missing])
    v = GroundingValidator(provider)
    result = await v.validate(
        "Claim alpha. Claim beta.",
        tool_results=[{"name": "x", "content": "y"}],
        retrieved_chunks=[],
    )
    assert provider.call_count == 2  # original + retry
    assert result.ran is True
    assert result.supported_count == 2
    assert result.uncertain_count == 0  # nothing fell through to the fallback


@pytest.mark.asyncio
async def test_partial_judge_response_falls_back_to_uncertain() -> None:
    """If retry is ALSO partial, missing claims default to uncertain
    (yellow caution) — never silently to no-chip unverifiable."""
    first_partial = json.dumps(
        [
            {"index": 1, "verdict": "supported", "reason": "ok"},
        ]
    )
    second_also_partial = json.dumps([])  # nothing new
    provider = _ScriptedProvider([first_partial, second_also_partial])
    v = GroundingValidator(provider)
    result = await v.validate(
        "Claim alpha. Claim beta.",
        tool_results=[{"name": "x", "content": "y"}],
        retrieved_chunks=[],
    )
    assert provider.call_count == 2
    assert result.ran is True
    assert result.supported_count == 1
    # The unjudged claim becomes uncertain (yellow), NOT silent unverifiable.
    assert result.uncertain_count == 1
    assert result.unverifiable_count == 0
    # Reason carries the diagnostic for the audit trail.
    unjudged = next(c for c in result.claims if c.verdict == "uncertain")
    assert "judge returned no verdict" in unjudged.reason


class _FlakeyProvider:
    """Stub that returns a scripted mix of content strings AND exceptions
    so we can simulate a transient first-call failure (Slice 5.0b.3)."""

    def __init__(self, script: list[str | Exception]) -> None:
        self._script = list(script)
        self.call_count = 0

    def capability(self) -> Any:
        class _C:
            model_id = "stub"
            provider = "stub"

        return _C()

    async def chat(self, request: ChatRequest) -> ChatResponse:
        idx = min(self.call_count, len(self._script) - 1)
        item = self._script[idx]
        self.call_count += 1
        if isinstance(item, Exception):
            raise item
        return ChatResponse(
            content=item,
            tool_calls=[],
            input_tokens=0,
            output_tokens=0,
            stop_reason="stop",
            model_id="stub",
        )


@pytest.mark.asyncio
async def test_judge_recovers_when_first_call_raises() -> None:
    """A transient first-call failure (e.g. ReadTimeout on cold model) is
    retried once; the warm second call succeeds. Slice 5.0b.3 persistence."""
    second_ok = json.dumps(
        [
            {"index": 0, "verdict": "supported", "reason": "ok"},
        ]
    )
    provider = _FlakeyProvider([RuntimeError("simulated timeout"), second_ok])
    v = GroundingValidator(provider)
    result = await v.validate(
        "Rule 5712 fires on 8 SSH failures.",
        tool_results=[{"name": "x", "content": "y"}],
        retrieved_chunks=[],
    )
    assert provider.call_count == 2
    assert result.ran is True
    assert result.supported_count == 1


@pytest.mark.asyncio
async def test_full_judge_response_does_not_retry() -> None:
    """Happy path: every claim was judged → no second call to the judge."""
    full = json.dumps(
        [
            {"index": 0, "verdict": "supported", "reason": "a"},
            {"index": 1, "verdict": "supported", "reason": "b"},
        ]
    )
    provider = _ScriptedProvider([full])
    v = GroundingValidator(provider)
    result = await v.validate(
        "Claim alpha. Claim beta.",
        tool_results=[{"name": "x", "content": "y"}],
        retrieved_chunks=[],
    )
    assert provider.call_count == 1  # no retry
    assert result.supported_count == 2


@pytest.mark.asyncio
async def test_empty_judge_content_raises_clear_error_then_falls_back() -> None:
    """qwen3:8b sometimes returns empty content under context pressure.

    The judge call now raises a clear 'empty content' ValueError rather
    than the misleading 'Expecting value: line 1 column 1 (char 0)' from
    `json.loads('')`. The validator retries once; if still empty, all
    claims fall back to `uncertain` (yellow) via the 5.0b.2 fallback —
    never silently to `unverifiable` (Slice 5.0b.4).
    """
    provider = _StubProvider("")  # empty both calls
    v = GroundingValidator(provider)
    result = await v.validate(
        "Rule 5712 fires on 8 SSH failures.",
        tool_results=[{"name": "x", "content": "y"}],
        retrieved_chunks=[],
    )
    assert result.ran is False
    # Falls through to the un-annotated answer; no fake verdicts emitted.
    assert result.annotated_answer == "Rule 5712 fires on 8 SSH failures."


def test_validator_default_max_claims_is_12() -> None:
    """Slice 5.0b.4 lowered the cap from 20 so the judge's input fits
    qwen3:8b's Ollama context window with comfortable headroom."""
    v = GroundingValidator(_StubProvider("[]"))
    assert v._max_claims == 12


def test_validator_prompt_covers_meta_commentary_and_paraphrase() -> None:
    """Sanity-check that the Slice 5.0b.1 prompt sharpenings are in place.

    The judge's calibration over real answers is verified empirically (live
    self-validation); this test just guards against accidentally reverting
    the prompt to a definition that misses these cases.
    """
    from wolf_server.grounding.validator import VALIDATOR_SYSTEM_PROMPT

    # Broader unverifiable: meta commentary about Wolf's own analysis flow.
    assert "No further tool calls are needed" in VALIDATOR_SYSTEM_PROMPT
    assert "meta commentary" in VALIDATOR_SYSTEM_PROMPT
    # Paraphrase-is-supported softening.
    assert "paraphrase" in VALIDATOR_SYSTEM_PROMPT.lower()
    # Safety rule preserved: fabricated specifics still UNSUPPORTED.
    assert "UNSUPPORTED, never UNCERTAIN" in VALIDATOR_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_validate_skips_when_no_citations_implicit_no_evidence() -> None:
    """build_evidence returns '' when both inputs empty; validator skips."""
    provider = _StubProvider("[]")
    v = GroundingValidator(provider)
    result = await v.validate(
        "Some answer here.",
        tool_results=[],
        retrieved_chunks=[],
    )
    assert result.ran is False
    assert result.annotated_answer == "Some answer here."
    # Judge was never called.
    assert provider.last_request is None


@pytest.mark.asyncio
async def test_validate_skips_empty_answer() -> None:
    provider = _StubProvider("[]")
    v = GroundingValidator(provider)
    result = await v.validate(
        "",
        tool_results=[{"name": "x", "content": "y"}],
        retrieved_chunks=[],
    )
    assert result.ran is False
    assert provider.last_request is None


# ─── validate() — failure modes ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_judge_failure_returns_original_answer() -> None:
    """A judge LLM call that raises must degrade gracefully — Phase 3
    grounding posture is 'flag what we can verify, not block on failure'."""

    class _BoomProvider(_StubProvider):
        async def chat(self, request: ChatRequest) -> ChatResponse:
            raise RuntimeError("model timed out")

    v = GroundingValidator(_BoomProvider())
    result = await v.validate(
        "Rule 5712 fires on 8 SSH failures.",
        tool_results=[{"name": "x", "content": "y"}],
        retrieved_chunks=[],
    )
    assert result.ran is False
    assert result.annotated_answer == "Rule 5712 fires on 8 SSH failures."


@pytest.mark.asyncio
async def test_validate_malformed_json_returns_original_answer() -> None:
    """Judge returned text that isn't valid JSON — skip validation."""
    provider = _StubProvider("I think it's all fine actually.")
    v = GroundingValidator(provider)
    result = await v.validate(
        "Some claim.",
        tool_results=[{"name": "x", "content": "y"}],
        retrieved_chunks=[],
    )
    assert result.ran is False


@pytest.mark.asyncio
async def test_validate_strips_json_codefence_wrapping() -> None:
    """Some small models wrap their JSON in a markdown fence; tolerated."""
    provider = _StubProvider('```json\n[{"index": 0, "verdict": "supported", "reason": "ok"}]\n```')
    v = GroundingValidator(provider)
    result = await v.validate(
        "One claim only.",
        tool_results=[{"name": "x", "content": "y"}],
        retrieved_chunks=[],
    )
    assert result.ran is True
    assert result.supported_count == 1


@pytest.mark.asyncio
async def test_validate_clamps_claim_count() -> None:
    """Runaway-long answers don't get an unbounded judge prompt."""
    provider = _StubProvider("[]")
    v = GroundingValidator(provider, max_claims=3)
    # Build a long answer with 10 sentences.
    answer = " ".join(f"Sentence number {i} is here." for i in range(10))
    await v.validate(
        answer,
        tool_results=[{"name": "x", "content": "y"}],
        retrieved_chunks=[],
    )
    # ran=False because the empty-array judge response = no verdicts.
    # But we should have only sent 3 claims to the judge.
    assert provider.last_request is not None
    user_msg = provider.last_request.messages[1].content
    # Count the numbered claim lines (the prompt format is "N. claim").
    claim_lines = [
        line for line in user_msg.splitlines() if line and line[0].isdigit() and ". " in line[:5]
    ]
    assert len(claim_lines) == 3


# ─── annotate() — unit-level ────────────────────────────────────────────────


def test_annotate_inserts_one_marker_per_verdict() -> None:
    """Slice 5.0c-a: every verdict now gets an inline marker so the
    analyst sees a signal for every claim — green Verified, yellow
    Uncertain, red Not Verified, muted Non-factual.
    """
    from wolf_server.grounding.validator import ClaimVerdict

    answer = "Claim one. Claim two. Claim three. Claim four."
    verdicts = [
        ClaimVerdict(claim="Claim one.", verdict="supported"),
        ClaimVerdict(claim="Claim two.", verdict="unsupported"),
        ClaimVerdict(claim="Claim three.", verdict="uncertain"),
        ClaimVerdict(claim="Claim four.", verdict="unverifiable"),
    ]
    out = GroundingValidator._annotate(answer, verdicts)
    assert "Claim one. [verified]" in out  # green
    assert "Claim two. [unsupported]" in out  # red
    assert "Claim three. [unverified]" in out  # yellow caution
    assert "Claim four. [non-factual]" in out  # muted (preamble)


def test_annotate_marks_supported_with_green_verified() -> None:
    """Slice 5.0c-a: supported claims now also get a chip ([verified] →
    green) so the analyst sees positive grounding signals too, not just
    warnings.
    """
    from wolf_server.grounding.validator import ClaimVerdict

    answer = "Everything is fine."
    out = GroundingValidator._annotate(
        answer,
        [
            ClaimVerdict(claim="Everything is fine.", verdict="supported"),
        ],
    )
    assert out == "Everything is fine. [verified]"


# ─── ValidationResult shape ─────────────────────────────────────────────────


def test_validation_result_default_state() -> None:
    r = ValidationResult()
    assert r.claims == []
    assert r.annotated_answer == ""
    assert r.ran is False
    assert r.supported_count == 0
    assert r.unsupported_count == 0
    assert r.uncertain_count == 0
    assert r.unverifiable_count == 0


# ─── ADR 0026 — incremental (batched, concurrent) grounding ─────────────────


@pytest.mark.asyncio
async def test_validate_streaming_progressive_then_final() -> None:
    """Incremental mode yields a cumulative snapshot per completed batch and
    a final complete snapshot; batch-local indices map back to global slots."""
    # batch_size=1 → one batch per claim; stub returns the local index 0.
    provider = _StubProvider('[{"index": 0, "verdict": "supported"}]')
    v = GroundingValidator(provider)
    snapshots = [
        s
        async for s in v.validate_streaming(
            "Agent 001 is active. Rule 5712 fired. The IP was blocked.",
            tool_results=[{"name": "get_agent_detail", "content": {"id": "001"}}],
            retrieved_chunks=[],
            batch_size=1,
        )
    ]
    # 3 batches → 3 progressive yields + 1 final yield.
    assert len(snapshots) == 4
    final = snapshots[-1]
    assert final.ran is True
    assert final.supported_count == 3  # all three global slots filled
    # Cumulative counts are monotonic non-decreasing across the snapshots.
    counts = [s.supported_count for s in snapshots]
    assert counts == sorted(counts)
    assert counts[-1] == 3


@pytest.mark.asyncio
async def test_validate_streaming_yields_nothing_when_no_evidence() -> None:
    """No evidence (and a non-empty answer) → nothing to verify → no yields,
    matching the single-call validate() skip."""
    provider = _StubProvider('[{"index": 0, "verdict": "supported"}]')
    v = GroundingValidator(provider)
    snapshots = [
        s
        async for s in v.validate_streaming(
            "Some claim with no backing evidence.",
            tool_results=[],
            retrieved_chunks=[],
        )
    ]
    assert snapshots == []


@pytest.mark.asyncio
async def test_validate_streaming_failed_batch_fills_uncertain() -> None:
    """A batch whose judge call raises must not sink the answer — its claims
    fall back to `uncertain` (always a signal) and the final snapshot still
    runs."""

    class _BoomProvider(_StubProvider):
        async def chat(self, request: ChatRequest) -> ChatResponse:
            raise RuntimeError("judge down")

    v = GroundingValidator(_BoomProvider())
    snapshots = [
        s
        async for s in v.validate_streaming(
            "Agent 001 is active. Rule 5712 fired.",
            tool_results=[{"name": "get_agent_detail", "content": {"id": "001"}}],
            retrieved_chunks=[],
            batch_size=1,
        )
    ]
    final = snapshots[-1]
    assert final.ran is True
    assert final.uncertain_count == 2  # both claims filled uncertain
    assert final.supported_count == 0
