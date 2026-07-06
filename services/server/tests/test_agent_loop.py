"""Tests for the agent loop with a scripted MockProvider.

The MockProvider returns a queued sequence of ChatResponses so we can
deterministically drive the loop through:
  - immediate-answer (no tool calls) → stop_reason="answer"
  - tool-call → tool-result → final-answer (citations aggregated)
  - unbounded persistence (6-f.5): the loop sails past the model's graded
    step count (a soft checkpoint, never a wall) and every forced stop
    (no progress / operator breaker / context-fit) SYNTHESIZES a best-effort
    answer — "budget_exhausted" no longer exists
  - provider raises → stop_reason="loop_error"
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_schema import CapabilityDescriptor, ChatRequest, ChatResponse, ToolCall
from wolf_schema.capability import (
    AgentStrategy,
    NativeToolCalling,
    ReasoningTier,
    StructuredOutput,
)
from wolf_server.agent.loop import AgentLoop
from wolf_server.agent.strategies import FrontierStrategy, PipelineStrategy
from wolf_server.audit.models import AuditEvent
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
        session_id="sess-loop-1",
    )


@pytest.fixture(autouse=True)
def isolated_registries() -> Iterator[None]:
    """Reset the singletons before AND after each test."""
    from wolf_server.models.registry import registry as schema_registry
    from wolf_server.tools.registry import runtime_registry

    schema_registry.clear()
    runtime_registry.clear()
    yield
    schema_registry.clear()
    runtime_registry.clear()


def _register_search_alerts() -> None:
    from wolf_server.tools.registry import runtime_registry

    runtime_registry.register(SearchAlertsTool())


def _fake_clients() -> tuple[MagicMock, MagicMock]:
    """OpenSearch + Server API mocks suitable for SearchAlertsTool."""
    os_client = MagicMock()
    os_client.query_builder.search_alerts.return_value = {"query": {"bool": {"filter": []}}}
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
    db: AsyncSession, organization_ctx: OrganizationContext
) -> None:
    provider = MockProvider([_response("The answer is 42.")])
    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()

    answer = await loop.run(
        question="hello",
        ctx=organization_ctx,
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
        (
            await db.execute(
                select(AuditEvent)
                .where(AuditEvent.event_type == "model.call.success")
                .where(AuditEvent.organization_id == organization_ctx.organization_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


# ─── Test: empty-answer recovery (Slice 5.0b reliability) ────────────────────


@pytest.mark.asyncio
async def test_loop_recovers_empty_answer_via_synthesis_retry(
    db: AsyncSession, organization_ctx: OrganizationContext
) -> None:
    """An empty final answer triggers one no-tools re-prompt that recovers."""
    provider = MockProvider([_response(""), _response("Recovered summary.")])
    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()

    answer = await loop.run(
        question="hello",
        ctx=organization_ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
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
    db: AsyncSession, organization_ctx: OrganizationContext
) -> None:
    """If the recovery re-prompt is also empty, show an honest message, never blank."""
    from wolf_server.agent.loop import _EMPTY_ANSWER_FALLBACK

    provider = MockProvider([_response(""), _response("")])
    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()

    answer = await loop.run(
        question="hello",
        ctx=organization_ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
    )
    assert answer.content == _EMPTY_ANSWER_FALLBACK
    assert answer.content.strip()  # never blank
    assert provider.call_count == 2


# ─── Test: tool call → tool result → final answer ────────────────────────────


@pytest.mark.asyncio
async def test_loop_handles_tool_call_then_final_answer(
    db: AsyncSession, organization_ctx: OrganizationContext
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
        ctx=organization_ctx,
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
        (
            await db.execute(
                select(AuditEvent)
                .where(AuditEvent.event_type == "model.call.success")
                .where(AuditEvent.organization_id == organization_ctx.organization_id)
            )
        )
        .scalars()
        .all()
    )
    tool_calls = (
        (
            await db.execute(
                select(AuditEvent)
                .where(AuditEvent.event_type == "tool.call.success")
                .where(AuditEvent.organization_id == organization_ctx.organization_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(model_calls) == 2
    assert len(tool_calls) == 1


# ─── Tests: unbounded persistence (6-f.5) ────────────────────────────────────


def _distinct_tool_call_responses(count: int) -> list[ChatResponse]:
    """DISTINCT calls each step (varying time_from) so the repeated-tool-call
    guard does NOT trip — a model doing genuinely NEW work every step."""
    now = datetime.now(UTC)
    return [
        _response(
            tool_calls=[
                ToolCall(
                    id=f"c-loop-{i}",
                    name="search_alerts",
                    arguments={
                        "time_from": (now - timedelta(hours=i + 1)).isoformat(),
                        "time_to": now.isoformat(),
                    },
                )
            ],
            stop_reason="tool_use",
        )
        for i in range(count)
    ]


@pytest.mark.asyncio
async def test_loop_persists_past_the_graded_step_count(
    db: AsyncSession, organization_ctx: OrganizationContext
) -> None:
    """No hard step cap (operator directive 2026-07-06): a model making real
    progress runs PAST max_safe_autonomous_steps (5 here) — the grade is a
    soft take-stock checkpoint, never a wall — and still lands its answer."""
    _register_search_alerts()
    provider = MockProvider(
        [
            *_distinct_tool_call_responses(10),
            _response("After a long investigation, here is the full picture."),
        ]
    )
    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()

    answer = await loop.run(
        question="investigate deeply",
        ctx=organization_ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
    )
    assert answer.stop_reason == "answer"
    assert answer.step_count == 11  # 10 tool steps + the answer — past the old 5-step wall
    assert answer.tool_call_count == 10
    assert "full picture" in answer.content
    # The checkpoint nudge was injected at the graded cadence (steps 5 and 10).
    assert provider.last_request is not None
    nudges = [
        m
        for m in provider.last_request.messages
        if m.content and m.content.startswith("Checkpoint —")
    ]
    assert len(nudges) == 2


@pytest.mark.asyncio
async def test_step_breaker_forces_best_effort_synthesis(
    db: AsyncSession, organization_ctx: OrganizationContext
) -> None:
    """The OPTIONAL operator circuit breaker: reaching it forces a synthesis
    from the gathered evidence — a real answer, never a canned failure."""
    _register_search_alerts()
    provider = MockProvider(
        [
            *_distinct_tool_call_responses(3),
            _response("Best-effort summary from the evidence gathered so far."),
        ]
    )
    loop = AgentLoop(provider=provider, strategy=FrontierStrategy(), step_breaker=3)
    os_client, server_api = _fake_clients()

    answer = await loop.run(
        question="investigate",
        ctx=organization_ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
    )
    assert answer.stop_reason == "answer"
    assert answer.step_count == 3
    assert answer.tool_call_count == 3
    assert "Best-effort summary" in answer.content
    # The synthesis call carries the forced-synthesis instruction and NO tools.
    assert provider.last_request is not None
    assert provider.last_request.tools is None
    assert any(
        m.content and "best possible final answer" in m.content
        for m in provider.last_request.messages
    )


@pytest.mark.asyncio
async def test_context_fit_guard_synthesizes_before_the_window_overflows(
    db: AsyncSession, organization_ctx: OrganizationContext
) -> None:
    """When the transcript nears the model's effective context window
    (8192 × 0.8 = 6553 here), the loop stops gathering — further evidence
    physically cannot fit — and synthesizes from what it has."""
    _register_search_alerts()
    now = datetime.now(UTC)
    big_step = ChatResponse(
        content="",
        tool_calls=[
            ToolCall(
                id="c-big",
                name="search_alerts",
                arguments={
                    "time_from": (now - timedelta(hours=1)).isoformat(),
                    "time_to": now.isoformat(),
                },
            )
        ],
        input_tokens=7000,  # ≥ the 6553 fit limit
        output_tokens=20,
        stop_reason="tool_use",
        model_id="mock-model",
    )
    provider = MockProvider([big_step, _response("Answer composed from the evidence that fits.")])
    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()

    answer = await loop.run(
        question="investigate",
        ctx=organization_ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
    )
    assert answer.stop_reason == "answer"
    assert answer.step_count == 1
    assert "evidence that fits" in answer.content


# ─── Test: repeated-tool-call guard ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_guard_forces_synthesis_on_repeated_identical_tool_calls(
    db: AsyncSession, organization_ctx: OrganizationContext
) -> None:
    """A model that repeats the SAME tool call (same name + args) is stopped by
    the repeated-tool-call guard: it's nudged, and after two consecutive
    redundant steps a final answer is synthesized from what it already has —
    the no-progress stop (since 6-f.5 the loop's primary guard: with no fixed
    step ceiling, a looping model must be stopped by its lack of progress)."""
    _register_search_alerts()
    now = datetime.now(UTC)
    identical = ToolCall(
        id="c-dup",
        name="search_alerts",
        arguments={
            "time_from": (now - timedelta(hours=1)).isoformat(),
            "time_to": now.isoformat(),
        },
    )
    provider = MockProvider(
        [
            _response(tool_calls=[identical], stop_reason="tool_use"),  # step 0: new
            _response(tool_calls=[identical], stop_reason="tool_use"),  # step 1: repeat → nudge
            _response(tool_calls=[identical], stop_reason="tool_use"),  # step 2: repeat → trip
            _response("Based on the alerts I already retrieved, here is the summary."),
        ]
    )
    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()

    answer = await loop.run(
        question="keep searching the same thing",
        ctx=organization_ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
    )
    # Forced synthesis returns a real answer as soon as progress stops.
    assert answer.stop_reason == "answer"
    assert answer.step_count == 3
    assert "summary" in answer.content.lower()
    assert answer.tool_call_count == 3


# ─── Test: provider raises mid-loop ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_returns_loop_error_when_provider_raises(
    db: AsyncSession, organization_ctx: OrganizationContext
) -> None:
    provider = MockProvider([], raise_on_call=0)
    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()

    answer = await loop.run(
        question="boom",
        ctx=organization_ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
    )
    assert answer.stop_reason == "loop_error"
    # The user-facing content is clean and non-leaky — no raw provider/
    # exception text in the chat. The raw detail is preserved in the audit
    # record instead (asserted below).
    assert "couldn't complete" in answer.content.lower()
    assert "synthetic" not in answer.content

    await db.commit()
    rows = (
        (
            await db.execute(
                select(AuditEvent)
                .where(AuditEvent.event_type == "model.call.failure")
                .where(AuditEvent.organization_id == organization_ctx.organization_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    # The raw failure detail lives in the audit record (forensics), not the chat.
    assert "synthetic" in (rows[0].event_data or {})["detail"]


# ─── Test: pipeline strategy makes exactly one call with no tools ────────────


@pytest.mark.asyncio
async def test_pipeline_strategy_runs_one_step_with_no_tools_in_request(
    db: AsyncSession, organization_ctx: OrganizationContext
) -> None:
    provider = MockProvider(
        [_response("Based on the context provided, no anomalies are evident.")],
        descriptor=_descriptor(AgentStrategy.pipeline),
    )
    loop = AgentLoop(provider=provider, strategy=PipelineStrategy())
    os_client, server_api = _fake_clients()

    answer = await loop.run(
        question="summarize",
        ctx=organization_ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
    )
    assert answer.stop_reason == "answer"
    assert answer.step_count == 1
    assert provider.last_request is not None
    assert provider.last_request.tools is None  # pipeline exposes no tools


# ─── Test: retry_nudge appends the nudge to the user message ────────────────


@pytest.mark.asyncio
async def test_retry_nudge_appends_critique_hint_to_user_message(
    db: AsyncSession, organization_ctx: OrganizationContext
) -> None:
    """Slice 5.0c-g: Retry-on-Wolf-response flips retry_nudge=True on the
    chat request. The loop must append the RETRY_NUDGE text to the
    fresh user message it sends to the model so the model knows to
    critique its previous attempt (which wolf-dashboard put in history)."""
    from wolf_server.agent.prompts import RETRY_NUDGE

    provider = MockProvider([_response("Improved answer.")])
    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()

    await loop.run(
        question="any failed logins?",
        ctx=organization_ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
        history=[
            ("user", "any failed logins?"),
            ("assistant", "No failed logins in the last hour."),
        ],
        retry_nudge=True,
    )

    assert provider.last_request is not None
    last_user = next(m for m in reversed(provider.last_request.messages) if m.role == "user")
    assert "any failed logins?" in last_user.content
    assert RETRY_NUDGE.strip() in last_user.content


@pytest.mark.asyncio
async def test_retry_nudge_default_false_leaves_user_message_unchanged(
    db: AsyncSession, organization_ctx: OrganizationContext
) -> None:
    """Sanity check: without retry_nudge, the user message is the bare
    question — no nudge text leaks into the normal path."""
    from wolf_server.agent.prompts import RETRY_NUDGE

    provider = MockProvider([_response("Fresh answer.")])
    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()

    await loop.run(
        question="any failed logins?",
        ctx=organization_ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
    )

    assert provider.last_request is not None
    last_user = next(m for m in reversed(provider.last_request.messages) if m.role == "user")
    assert last_user.content == "any failed logins?"
    assert RETRY_NUDGE.strip() not in last_user.content


# ─── Test: empty/interrupted history turns are not replayed ─────────────────


@pytest.mark.asyncio
async def test_loop_skips_empty_history_turns(
    db: AsyncSession, organization_ctx: OrganizationContext
) -> None:
    """An interrupted prior turn ("no response generated before stop") is stored
    with empty content; replaying it as an empty-content message makes strict
    providers reject the whole request with 400 "invalid message". The loop must
    drop empty/whitespace history turns (a step-0 hard fail otherwise)."""
    provider = MockProvider([_response("ok")])
    loop = AgentLoop(provider=provider, strategy=FrontierStrategy())
    os_client, server_api = _fake_clients()

    await loop.run(
        question="follow-up question",
        ctx=organization_ctx,
        db=db,
        opensearch=os_client,
        server_api=server_api,
        history=[
            ("assistant", "   "),  # whitespace-only (interrupted) → dropped
            ("user", "earlier question"),  # real turn → kept
            ("assistant", ""),  # empty (interrupted) → dropped
        ],
    )

    assert provider.last_request is not None
    contents = [m.content for m in provider.last_request.messages]
    # The real prior turn survived; no empty/whitespace turn was replayed.
    assert "earlier question" in contents
    assert "" not in contents
    assert "   " not in contents
