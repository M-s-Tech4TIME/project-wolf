"""Tests for the agent loop with a scripted MockProvider.

The MockProvider returns a queued sequence of ChatResponses so we can
deterministically drive the loop through:
  - immediate-answer (no tool calls) → stop_reason="answer"
  - tool-call → tool-result → final-answer (citations aggregated)
  - exhausted budget → stop_reason="budget_exhausted"
  - provider raises → stop_reason="loop_error"
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.agent.loop import AgentLoop
from app.agent.strategies import FrontierStrategy, PipelineStrategy
from app.audit.models import AuditEvent
from app.tenancy.context import TenantContext
from app.tools.alerts import SearchAlertsTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_schema import CapabilityDescriptor, ChatRequest, ChatResponse, ToolCall
from wolf_schema.capability import (
    AgentStrategy,
    NativeToolCalling,
    ReasoningTier,
    StructuredOutput,
)


@pytest.fixture
def tenant_ctx() -> TenantContext:
    return TenantContext(
        tenant_id=uuid.uuid4(),
        tenant_slug="testco",
        user_id=uuid.uuid4(),
        user_email="analyst@test.example",
        role="analyst",
        session_id="sess-loop-1",
    )


@pytest.fixture(autouse=True)
def isolated_registries() -> Iterator[None]:
    """Reset the singletons before AND after each test."""
    from app.models.registry import registry as schema_registry
    from app.tools.registry import runtime_registry

    schema_registry.clear()
    runtime_registry.clear()
    yield
    schema_registry.clear()
    runtime_registry.clear()


def _register_search_alerts() -> None:
    from app.tools.registry import runtime_registry

    runtime_registry.register(SearchAlertsTool())


def _fake_clients() -> tuple[MagicMock, MagicMock]:
    """OpenSearch + Server API mocks suitable for SearchAlertsTool."""
    os_client = MagicMock()
    os_client.query_builder.search_alerts.return_value = {
        "query": {"bool": {"filter": []}}
    }
    os_client.execute = AsyncMock(
        return_value={
            "hits": {
                "total": {"value": 1},
                "hits": [
                    {
                        "_id": "alert1",
                        "_source": {
                            "timestamp": "2026-05-21T10:00:00Z",
                            "agent": {"id": "001", "name": "web-07"},
                            "rule": {"id": "5710", "level": 10, "description": "Failed login"},
                            "full_log": "Apr 21 10:00 sshd[1234]: Failed password",
                        },
                    }
                ],
            }
        }
    )
    server_api = MagicMock()
    return os_client, server_api


def _descriptor(strategy: AgentStrategy = AgentStrategy.frontier) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        model_id="mock-model",
        provider="mock",
        context_window=8192,
        native_tool_calling=NativeToolCalling.full,
        reasoning_tier=ReasoningTier.frontier,
        structured_output=StructuredOutput.schema_enforced,
        max_safe_autonomous_steps=5,
        recommended_strategy=strategy,
    )


class MockProvider:
    """ModelProvider that replays a scripted sequence of ChatResponses."""

    def __init__(
        self,
        responses: list[ChatResponse],
        *,
        descriptor: CapabilityDescriptor | None = None,
        raise_on_call: int | None = None,
    ) -> None:
        self._responses = list(responses)
        self._descriptor = descriptor or _descriptor()
        self._raise_on_call = raise_on_call
        self.call_count = 0
        self.last_request: ChatRequest | None = None

    def capability(self) -> CapabilityDescriptor:
        return self._descriptor

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.last_request = request
        if self._raise_on_call is not None and self.call_count == self._raise_on_call:
            self.call_count += 1
            raise RuntimeError("synthetic model failure")
        if self.call_count >= len(self._responses):
            raise AssertionError("MockProvider exhausted its scripted responses")
        response = self._responses[self.call_count]
        self.call_count += 1
        return response

    def stream(self, request: ChatRequest) -> Any:  # noqa: ARG002 — protocol surface
        raise NotImplementedError


def _response(
    content: str = "",
    tool_calls: list[ToolCall] | None = None,
    *,
    stop_reason: str = "end_turn",
) -> ChatResponse:
    return ChatResponse(
        content=content,
        tool_calls=tool_calls or [],
        input_tokens=10,
        output_tokens=20,
        stop_reason=stop_reason,
        model_id="mock-model",
    )


# ─── Test: immediate answer ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_returns_immediate_answer_when_no_tool_calls(
    db: AsyncSession, tenant_ctx: TenantContext
) -> None:
    provider = MockProvider([_response("The answer is 42.")])
    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()

    answer = await loop.run(
        question="hello",
        ctx=tenant_ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
    )
    assert answer.content == "The answer is 42."
    assert answer.stop_reason == "answer"
    assert answer.step_count == 1
    assert answer.tool_call_count == 0
    assert answer.citations == []
    assert answer.input_tokens == 10
    assert answer.output_tokens == 20

    await db.commit()
    rows = (
        await db.execute(
            select(AuditEvent)
            .where(AuditEvent.event_type == "model.call.success")
            .where(AuditEvent.tenant_id == tenant_ctx.tenant_id)
        )
    ).scalars().all()
    assert len(rows) == 1


# ─── Test: empty-answer recovery (Slice 5.0b reliability) ────────────────────


@pytest.mark.asyncio
async def test_loop_recovers_empty_answer_via_synthesis_retry(
    db: AsyncSession, tenant_ctx: TenantContext
) -> None:
    """An empty final answer triggers one no-tools re-prompt that recovers."""
    provider = MockProvider([_response(""), _response("Recovered summary.")])
    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()

    answer = await loop.run(
        question="hello", ctx=tenant_ctx, db=db,
        opensearch=os_client, server_api=server_api,
    )
    assert answer.content == "Recovered summary."
    assert provider.call_count == 2  # original + one synthesis retry
    # The retry must omit tools so the model writes prose, not a tool call.
    assert provider.last_request is not None
    assert not provider.last_request.tools
    # Retry tokens are accounted on top of the first call.
    assert answer.input_tokens == 20
    assert answer.output_tokens == 40


@pytest.mark.asyncio
async def test_loop_empty_answer_falls_back_when_retry_also_empty(
    db: AsyncSession, tenant_ctx: TenantContext
) -> None:
    """If the recovery re-prompt is also empty, show an honest message, never blank."""
    from app.agent.loop import _EMPTY_ANSWER_FALLBACK

    provider = MockProvider([_response(""), _response("")])
    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()

    answer = await loop.run(
        question="hello", ctx=tenant_ctx, db=db,
        opensearch=os_client, server_api=server_api,
    )
    assert answer.content == _EMPTY_ANSWER_FALLBACK
    assert answer.content.strip()  # never blank
    assert provider.call_count == 2


# ─── Test: tool call → tool result → final answer ────────────────────────────


@pytest.mark.asyncio
async def test_loop_handles_tool_call_then_final_answer(
    db: AsyncSession, tenant_ctx: TenantContext
) -> None:
    _register_search_alerts()
    now = datetime.now(UTC)
    call = ToolCall(
        id="c-1",
        name="search_alerts",
        arguments={
            "time_from": (now - timedelta(hours=1)).isoformat(),
            "time_to": now.isoformat(),
        },
    )
    provider = MockProvider(
        [
            _response(tool_calls=[call], stop_reason="tool_use"),
            _response("Found 1 alert on web-07 for rule 5710 (failed login)."),
        ]
    )
    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()

    answer = await loop.run(
        question="any failed logins?",
        ctx=tenant_ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
    )
    assert answer.stop_reason == "answer"
    assert answer.step_count == 2
    assert answer.tool_call_count == 1
    assert len(answer.citations) == 1
    assert answer.citations[0].tool == "search_alerts"
    assert "web-07" in answer.content

    await db.commit()
    model_calls = (
        await db.execute(
            select(AuditEvent)
            .where(AuditEvent.event_type == "model.call.success")
            .where(AuditEvent.tenant_id == tenant_ctx.tenant_id)
        )
    ).scalars().all()
    tool_calls = (
        await db.execute(
            select(AuditEvent)
            .where(AuditEvent.event_type == "tool.call.success")
            .where(AuditEvent.tenant_id == tenant_ctx.tenant_id)
        )
    ).scalars().all()
    assert len(model_calls) == 2
    assert len(tool_calls) == 1


# ─── Test: budget exhausted ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_returns_budget_exhausted_when_model_keeps_calling_tools(
    db: AsyncSession, tenant_ctx: TenantContext
) -> None:
    _register_search_alerts()
    now = datetime.now(UTC)
    call = ToolCall(
        id="c-loop",
        name="search_alerts",
        arguments={
            "time_from": (now - timedelta(hours=1)).isoformat(),
            "time_to": now.isoformat(),
        },
    )
    # Strategy budget = 5 steps; model loops a tool call forever.
    provider = MockProvider(
        [_response(tool_calls=[call], stop_reason="tool_use") for _ in range(10)]
    )
    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()

    answer = await loop.run(
        question="loop forever?",
        ctx=tenant_ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
    )
    assert answer.stop_reason == "budget_exhausted"
    assert answer.step_count == 5  # capability.max_safe_autonomous_steps
    assert answer.tool_call_count == 5


# ─── Test: provider raises mid-loop ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_returns_loop_error_when_provider_raises(
    db: AsyncSession, tenant_ctx: TenantContext
) -> None:
    provider = MockProvider([], raise_on_call=0)
    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()

    answer = await loop.run(
        question="boom",
        ctx=tenant_ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
    )
    assert answer.stop_reason == "loop_error"
    assert "synthetic" in answer.content

    await db.commit()
    rows = (
        await db.execute(
            select(AuditEvent)
            .where(AuditEvent.event_type == "model.call.failure")
            .where(AuditEvent.tenant_id == tenant_ctx.tenant_id)
        )
    ).scalars().all()
    assert len(rows) == 1


# ─── Test: pipeline strategy makes exactly one call with no tools ────────────


@pytest.mark.asyncio
async def test_pipeline_strategy_runs_one_step_with_no_tools_in_request(
    db: AsyncSession, tenant_ctx: TenantContext
) -> None:
    provider = MockProvider(
        [_response("Based on the context provided, no anomalies are evident.")],
        descriptor=_descriptor(AgentStrategy.pipeline),
    )
    loop = AgentLoop(provider=provider, strategy=PipelineStrategy())
    os_client, server_api = _fake_clients()

    answer = await loop.run(
        question="summarize",
        ctx=tenant_ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
    )
    assert answer.stop_reason == "answer"
    assert answer.step_count == 1
    assert provider.last_request is not None
    assert provider.last_request.tools is None  # pipeline exposes no tools
