"""Tests for the model probe — tasks and grader.

All tests use a mock ModelProvider so no real API credentials are needed.
"""

from typing import Any

import pytest
from wolf_schema import ChatRequest, ChatResponse, ToolCall
from wolf_schema.capability import (
    AgentStrategy,
    CapabilityDescriptor,
    NativeToolCalling,
    ReasoningTier,
    StructuredOutput,
)


def _make_descriptor(
    model_id: str = "test-model",
    provider: str = "test",
    tier: ReasoningTier = ReasoningTier.mid,
    tool_calling: NativeToolCalling = NativeToolCalling.full,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        model_id=model_id,
        provider=provider,
        context_window=8192,
        native_tool_calling=tool_calling,
        reasoning_tier=tier,
        structured_output=StructuredOutput.prompt_coaxed,
        max_safe_autonomous_steps=8,
        recommended_strategy=AgentStrategy.guided,
    )


class MockProvider:
    """Configurable mock that returns preset responses for probe tasks."""

    def __init__(
        self,
        *,
        tool_name: str = "ping",
        json_response: str = '{"severity": "high", "summary": "test", "agent_id": "a-001"}',
        first_tool: str = "lookup_host",
        grounding_refusal: str = "I don't have access to query that data",
    ) -> None:
        self._tool_name = tool_name
        self._json_response = json_response
        self._first_tool = first_tool
        self._grounding_refusal = grounding_refusal
        self._desc = _make_descriptor()

    def capability(self) -> CapabilityDescriptor:
        return self._desc

    async def chat(self, request: ChatRequest) -> ChatResponse:
        content = request.messages[-1].content if request.messages else ""

        # Grounding discipline task — no tools, asks for specific data
        if request.tools is None and "SSH login" in content:
            return ChatResponse(
                content=self._grounding_refusal,
                tool_calls=[],
                input_tokens=20,
                output_tokens=10,
                stop_reason="stop",
                model_id="test",
            )

        # JSON schema adherence task
        if "JSON object" in content and request.tools is None:
            return ChatResponse(
                content=self._json_response,
                tool_calls=[],
                input_tokens=20,
                output_tokens=15,
                stop_reason="stop",
                model_id="test",
            )

        # Multi-step reasoning task (identified by its unique content)
        # For all other tool-call tasks, use self._tool_name.
        name = self._first_tool if "alpha.internal" in content else self._tool_name
        tool_calls: list[ToolCall] = [ToolCall(id="tc-1", name=name, arguments={})]
        return ChatResponse(
            content="",
            tool_calls=tool_calls,
            input_tokens=20,
            output_tokens=10,
            stop_reason="tool_use",
            model_id="test",
        )

    def stream(self, request: ChatRequest) -> Any:
        raise NotImplementedError


# ── Individual task tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_tool_call_correct_name() -> None:
    from tools.model_probe.tasks import task_tool_call_formatting

    provider = MockProvider(tool_name="ping")
    result = await task_tool_call_formatting(provider)
    assert result.passed
    assert result.score == 1.0


@pytest.mark.asyncio
async def test_task_tool_call_wrong_name() -> None:
    from tools.model_probe.tasks import task_tool_call_formatting

    provider = MockProvider(tool_name="wrong_tool")  # task expects "ping", gets "wrong_tool"
    result = await task_tool_call_formatting(provider)
    assert not result.passed
    assert result.score < 1.0


@pytest.mark.asyncio
async def test_task_json_schema_valid() -> None:
    from tools.model_probe.tasks import task_json_schema_adherence

    provider = MockProvider(
        json_response='{"severity": "high", "summary": "SSH brute force", "agent_id": "a-001"}'
    )
    result = await task_json_schema_adherence(provider)
    assert result.passed
    assert result.score == 1.0


@pytest.mark.asyncio
async def test_task_json_schema_invalid_severity() -> None:
    from tools.model_probe.tasks import task_json_schema_adherence

    provider = MockProvider(
        json_response='{"severity": "extreme", "summary": "bad", "agent_id": "a-001"}'
    )
    result = await task_json_schema_adherence(provider)
    assert not result.passed


@pytest.mark.asyncio
async def test_task_json_schema_not_json() -> None:
    from tools.model_probe.tasks import task_json_schema_adherence

    provider = MockProvider(json_response="Here is my answer in prose.")
    result = await task_json_schema_adherence(provider)
    assert not result.passed
    assert result.score == 0.0


@pytest.mark.asyncio
async def test_task_multi_step_correct_first_call() -> None:
    from tools.model_probe.tasks import task_multi_step_reasoning

    provider = MockProvider(first_tool="lookup_host")
    result = await task_multi_step_reasoning(provider)
    assert result.passed
    assert result.score == 1.0


@pytest.mark.asyncio
async def test_task_multi_step_wrong_first_call() -> None:
    from tools.model_probe.tasks import task_multi_step_reasoning

    provider = MockProvider(first_tool="scan_ip")
    result = await task_multi_step_reasoning(provider)
    assert not result.passed
    assert result.score <= 0.3


@pytest.mark.asyncio
async def test_task_grounding_discipline_passes() -> None:
    from tools.model_probe.tasks import task_grounding_discipline

    provider = MockProvider(
        grounding_refusal="I don't have access to query that data without tools"
    )
    result = await task_grounding_discipline(provider)
    assert result.passed


@pytest.mark.asyncio
async def test_task_grounding_discipline_fabrication_fails() -> None:
    from tools.model_probe.tasks import task_grounding_discipline

    provider = MockProvider(
        grounding_refusal="192.168.1.45 made 347 failed login attempts"
    )
    result = await task_grounding_discipline(provider)
    assert not result.passed
    assert result.score == 0.0


# ── Probe grader ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_probe_produces_report() -> None:
    from tools.model_probe.probe import run_probe

    provider = MockProvider()
    report = await run_probe(provider)

    assert report.model_id == "test-model"
    assert len(report.task_results) == 4
    assert 0.0 <= report.overall_score <= 1.0


@pytest.mark.asyncio
async def test_run_probe_high_score_yields_frontier_or_mid_strategy() -> None:
    from tools.model_probe.probe import run_probe

    # MockProvider passes tool-call, JSON, multi-step, and grounding — high score
    provider = MockProvider(first_tool="lookup_host")
    report = await run_probe(provider)

    assert report.measured_capability.recommended_strategy in (
        AgentStrategy.frontier,
        AgentStrategy.guided,
    )


def test_probe_report_summary_is_readable() -> None:
    from tools.model_probe.probe import run_probe_sync

    provider = MockProvider()
    report = run_probe_sync(provider)
    summary = report.summary()
    assert "test-model" in summary
    assert "reasoning_tier" in summary
