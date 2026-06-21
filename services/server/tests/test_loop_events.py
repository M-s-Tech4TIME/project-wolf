"""Tests for AgentLoop event emission — what the SSE endpoint relays.

Pins the order, types, and presence-of-key-fields for each event.
wolf-dashboard depends on this surface; changes here are deliberate.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_schema import CapabilityDescriptor, ChatRequest, ChatResponse, ToolCall
from wolf_schema.capability import (
    AgentStrategy,
    NativeToolCalling,
    ReasoningTier,
    StructuredOutput,
)
from wolf_server.agent.events import LoopEvent
from wolf_server.agent.loop import AgentLoop
from wolf_server.agent.strategies import FrontierStrategy
from wolf_server.organization.context import OrganizationContext
from wolf_server.tools.alerts import SearchAlertsTool


@pytest.fixture
def organization_ctx() -> OrganizationContext:
    return OrganizationContext(
        organization_id=uuid.uuid4(),
        organization_slug="testco",
        user_id=uuid.uuid4(),
        user_email="analyst@test.example",
        role="analyst",
        session_id="sess-event-1",
    )


@pytest.fixture(autouse=True)
def isolated_registries() -> Iterator[None]:
    from wolf_server.models.registry import registry as schema_registry
    from wolf_server.tools.registry import runtime_registry

    schema_registry.clear()
    runtime_registry.clear()
    yield
    schema_registry.clear()
    runtime_registry.clear()


def _descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        model_id="mock-model",
        provider="mock",
        context_window=8192,
        native_tool_calling=NativeToolCalling.full,
        reasoning_tier=ReasoningTier.frontier,
        structured_output=StructuredOutput.schema_enforced,
        max_safe_autonomous_steps=5,
        recommended_strategy=AgentStrategy.frontier,
    )


class ScriptedProvider:
    """Plays back a pre-recorded sequence of ChatResponses."""

    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)
        self._descriptor = _descriptor()
        self.call_count = 0

    def capability(self) -> CapabilityDescriptor:
        return self._descriptor

    async def chat(self, request: ChatRequest) -> ChatResponse:  # noqa: ARG002
        response = self._responses[self.call_count]
        self.call_count += 1
        return response

    def stream(self, request: ChatRequest) -> Any:  # noqa: ARG002
        raise NotImplementedError


def _response(content: str = "", tool_calls: list[ToolCall] | None = None) -> ChatResponse:
    return ChatResponse(
        content=content,
        tool_calls=tool_calls or [],
        input_tokens=10,
        output_tokens=20,
        stop_reason="end_turn" if not tool_calls else "tool_use",
        model_id="mock-model",
    )


def _fake_clients() -> tuple[MagicMock, MagicMock]:
    os_client = MagicMock()
    os_client.query_builder.search_alerts.return_value = {"query": {"bool": {"filter": []}}}
    os_client.execute = AsyncMock(return_value={"hits": {"total": {"value": 0}, "hits": []}})
    return os_client, MagicMock()


# ─── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_immediate_answer_emits_minimum_event_sequence(
    db: AsyncSession, organization_ctx: OrganizationContext
) -> None:
    events: list[LoopEvent] = []

    async def collect(event: LoopEvent) -> None:
        events.append(event)

    provider = ScriptedProvider([_response("Hello.")])
    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()

    await loop.run(
        question="hi",
        ctx=organization_ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
        event_callback=collect,
    )

    types = [e.type for e in events]
    assert types == [
        "loop.started",
        "step.started",
        "model.call.completed",
        "answer",
    ]
    # loop.started carries the strategy and model id.
    assert events[0].data["strategy"] == "frontier"
    assert events[0].data["model_id"] == "mock-model"
    # answer carries usage and stop reason.
    assert events[-1].data["stop_reason"] == "answer"
    assert events[-1].data["content"] == "Hello."


@pytest.mark.asyncio
async def test_tool_call_emits_tool_event_with_summary(
    db: AsyncSession, organization_ctx: OrganizationContext
) -> None:
    from wolf_server.tools.registry import runtime_registry

    runtime_registry.register(SearchAlertsTool())
    now = datetime.now(UTC)
    call = ToolCall(
        id="c-1",
        name="search_alerts",
        arguments={
            "time_from": (now - timedelta(hours=1)).isoformat(),
            "time_to": now.isoformat(),
        },
    )
    provider = ScriptedProvider(
        [
            _response(tool_calls=[call]),
            _response("Found nothing notable."),
        ]
    )
    events: list[LoopEvent] = []

    async def collect(event: LoopEvent) -> None:
        events.append(event)

    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()

    await loop.run(
        question="any alerts?",
        ctx=organization_ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
        event_callback=collect,
    )

    types = [e.type for e in events]
    # loop → step0 → model0 → tool_started → tool_done → step1 → model1 → answer
    # (Slice 5.0c-e: tool.call.started is announced BEFORE dispatch so
    # the live activity feed can narrate "Searching Wazuh…")
    assert types == [
        "loop.started",
        "step.started",
        "model.call.completed",
        "tool.call.started",
        "tool.call.completed",
        "step.started",
        "model.call.completed",
        "answer",
    ]
    tool_event = next(e for e in events if e.type == "tool.call.completed")
    assert tool_event.data["tool_name"] == "search_alerts"
    assert tool_event.data["success"] is True
    assert "citation" in tool_event.data
    assert tool_event.data["citation"]["tool"] == "search_alerts"


# ─── ADR 0026 — grounding execution modes ───────────────────────────────────


class _JudgeStub:
    """Minimal judge provider for the grounding validator — returns a fixed
    JSON verdict array on every chat() call."""

    def __init__(self, content: str) -> None:
        self._content = content

    def capability(self) -> CapabilityDescriptor:  # pragma: no cover — unused
        return _descriptor()

    async def chat(self, request: ChatRequest) -> ChatResponse:  # noqa: ARG002
        return ChatResponse(
            content=self._content,
            tool_calls=[],
            input_tokens=0,
            output_tokens=0,
            stop_reason="stop",
            model_id="judge-stub",
        )


async def _run_grounded(
    db: AsyncSession,
    ctx: OrganizationContext,
    *,
    mode: str,
) -> list[LoopEvent]:
    """Run a tool-backed answer (so grounding has citations to verify) under
    the given grounding mode, collecting the emitted events."""
    from wolf_server.grounding import GroundingValidator
    from wolf_server.tools.registry import runtime_registry

    runtime_registry.register(SearchAlertsTool())
    now = datetime.now(UTC)
    call = ToolCall(
        id="c-1",
        name="search_alerts",
        arguments={
            "time_from": (now - timedelta(hours=1)).isoformat(),
            "time_to": now.isoformat(),
        },
    )
    provider = ScriptedProvider(
        [_response(tool_calls=[call]), _response("Found nothing notable.")]
    )
    validator = GroundingValidator(_JudgeStub('[{"index": 0, "verdict": "supported"}]'))
    events: list[LoopEvent] = []

    async def collect(event: LoopEvent) -> None:
        events.append(event)

    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()
    await loop.run(
        question="any alerts?",
        ctx=ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
        event_callback=collect,
        grounding_validator=validator,
        grounding_mode=mode,
    )
    return events


@pytest.mark.asyncio
async def test_blocking_mode_grounds_before_answer(
    db: AsyncSession, organization_ctx: OrganizationContext
) -> None:
    """Default mode: the judge is awaited BEFORE the `answer` event, so the
    analyst never sees an answer marked complete until it's verified."""
    events = await _run_grounded(db, organization_ctx, mode="blocking")
    types = [e.type for e in events]
    assert types.count("answer") == 1
    ai = types.index("answer")
    # grounding ran and completed BEFORE the answer event.
    assert types.index("grounding.started") < ai
    assert types.index("grounding.completed") < ai
    answer_evt = events[ai]
    # blocking answer carries the counts inline, no pending flag, and the
    # grounding.completed event does NOT carry annotated_content.
    assert "grounding_pending" not in answer_evt.data
    assert answer_evt.data["grounding_supported"] == 1
    completed = next(e for e in events if e.type == "grounding.completed")
    assert "annotated_content" not in completed.data


@pytest.mark.asyncio
async def test_deferred_mode_answers_before_grounding(
    db: AsyncSession, organization_ctx: OrganizationContext
) -> None:
    """Deferred mode: the `answer` event fires immediately (raw + pending),
    the verdicts arrive afterwards carrying the annotated content to patch."""
    events = await _run_grounded(db, organization_ctx, mode="deferred")
    types = [e.type for e in events]
    assert types.count("answer") == 1
    ai = types.index("answer")
    # grounding starts + completes AFTER the answer event.
    assert types.index("grounding.started") > ai
    assert types.index("grounding.completed") > ai
    answer_evt = events[ai]
    assert answer_evt.data["grounding_pending"] is True
    # the late grounding event carries the annotated content to patch the
    # settled message + the counts.
    completed = next(e for e in events if e.type == "grounding.completed")
    assert "annotated_content" in completed.data
    assert completed.data["supported"] == 1


@pytest.mark.asyncio
async def test_incremental_mode_emits_partial_then_completed(
    db: AsyncSession, organization_ctx: OrganizationContext
) -> None:
    """Incremental mode: answer first, then ≥1 `grounding.partial` (progressive
    chips), then a final `grounding.completed`."""
    events = await _run_grounded(db, organization_ctx, mode="incremental")
    types = [e.type for e in events]
    ai = types.index("answer")
    assert events[ai].data["grounding_pending"] is True
    assert "grounding.partial" in types
    assert types.index("grounding.partial") > ai
    # partials carry the progressive annotated content.
    partial = next(e for e in events if e.type == "grounding.partial")
    assert "annotated_content" in partial.data
    # ends with a completed carrying the final annotated content.
    completed = next(e for e in events if e.type == "grounding.completed")
    assert "annotated_content" in completed.data
    assert types.index("grounding.completed") > types.index("grounding.partial")
