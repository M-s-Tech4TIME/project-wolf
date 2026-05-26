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
from app.grounding import GroundingValidator, ValidationResult
from wolf_schema import ChatRequest, ChatResponse


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
    provider = _StubProvider(json.dumps([
        {"index": 0, "verdict": "supported", "reason": "in evidence"},
        {"index": 1, "verdict": "supported", "reason": "in evidence"},
    ]))
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
    # No marker inserted on supported claims.
    assert "[unverified]" not in result.annotated_answer


@pytest.mark.asyncio
async def test_validate_marks_unsupported_claim_inline() -> None:
    """Canonical test: the embellishment we saw in Slice 1's mixed-mode."""
    provider = _StubProvider(json.dumps([
        {"index": 0, "verdict": "supported", "reason": "in evidence"},
        {"index": 1, "verdict": "unsupported", "reason": "not in evidence"},
    ]))
    v = GroundingValidator(provider)
    answer = (
        "Rule 5712 fires on 8 SSH failures within 120 seconds. "
        "Block the source IP for 60 seconds per the ignore parameter."
    )
    result = await v.validate(
        answer,
        tool_results=[{"name": "get_rule_definition", "content": {"id": 5712}}],
        retrieved_chunks=[{
            "source_type": "runbook",
            "chunk_metadata": {"title": "ACME"},
            "content": "Step 1: verify agent. Step 2: block at perimeter for "
                       "external IPs.",
        }],
    )
    assert result.ran is True
    assert result.supported_count == 1
    assert result.unsupported_count == 1
    # The marker lands AFTER the unsupported sentence, BEFORE the supported one.
    assert "[unverified]" in result.annotated_answer
    # And only the suspect sentence gets it.
    supported_idx = result.annotated_answer.find("Rule 5712 fires on 8 SSH failures")
    suspect_idx = result.annotated_answer.find("Block the source IP")
    marker_idx = result.annotated_answer.find("[unverified]")
    assert marker_idx > suspect_idx > supported_idx


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
    provider = _StubProvider(
        "```json\n[{\"index\": 0, \"verdict\": \"supported\", \"reason\": \"ok\"}]\n```"
    )
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
        line for line in user_msg.splitlines()
        if line and line[0].isdigit() and ". " in line[:5]
    ]
    assert len(claim_lines) == 3


# ─── annotate() — unit-level ────────────────────────────────────────────────


def test_annotate_inserts_marker_only_on_unsupported() -> None:
    from app.grounding.validator import ClaimVerdict

    answer = "Claim one. Claim two. Claim three."
    verdicts = [
        ClaimVerdict(claim="Claim one.", verdict="supported"),
        ClaimVerdict(claim="Claim two.", verdict="unsupported"),
        ClaimVerdict(claim="Claim three.", verdict="unverifiable"),
    ]
    out = GroundingValidator._annotate(answer, verdicts)
    assert "Claim one. [unverified]" not in out
    assert "Claim two. [unverified]" in out
    assert "Claim three. [unverified]" not in out


def test_annotate_no_unsupported_returns_original() -> None:
    from app.grounding.validator import ClaimVerdict

    answer = "Everything is fine."
    out = GroundingValidator._annotate(answer, [
        ClaimVerdict(claim="Everything is fine.", verdict="supported"),
    ])
    assert out == answer


# ─── ValidationResult shape ─────────────────────────────────────────────────


def test_validation_result_default_state() -> None:
    r = ValidationResult()
    assert r.claims == []
    assert r.annotated_answer == ""
    assert r.ran is False
    assert r.supported_count == 0
    assert r.unsupported_count == 0
    assert r.unverifiable_count == 0
