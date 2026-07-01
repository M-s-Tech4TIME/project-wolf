"""Tests for the provider-level failover chain (FailoverProvider).

Covers: transparent failover on a primary error, clean streaming failover
(before the first delta), the per-instance circuit-breaker, the conservative
capability floor, CancelledError propagation (Stop button), mid-stream re-raise,
and the all-links-failed path.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from wolf_schema import CapabilityDescriptor, ChatRequest, ChatResponse
from wolf_schema.capability import (
    AgentStrategy,
    NativeToolCalling,
    ReasoningTier,
    StructuredOutput,
)
from wolf_schema.chat import Message, MessageRole
from wolf_server.models.failover import FailoverProvider
from wolf_server.models.interface import ChatStreamDelta, ChatStreamDone, ChatStreamEvent
from wolf_server.models.openai import ModelProviderRateLimitError


def _desc(
    *,
    model_id: str = "m",
    provider: str = "p",
    steps: int = 15,
    strategy: AgentStrategy = AgentStrategy.frontier,
    tool: NativeToolCalling = NativeToolCalling.full,
    tier: ReasoningTier = ReasoningTier.frontier,
    so: StructuredOutput = StructuredOutput.schema_enforced,
    ctx: int = 200_000,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        model_id=model_id,
        provider=provider,
        context_window=ctx,
        native_tool_calling=tool,
        reasoning_tier=tier,
        structured_output=so,
        max_safe_autonomous_steps=steps,
        recommended_strategy=strategy,
    )


def _resp(content: str, model_id: str = "m") -> ChatResponse:
    return ChatResponse(
        content=content,
        tool_calls=[],
        input_tokens=1,
        output_tokens=1,
        stop_reason="stop",
        model_id=model_id,
    )


class _Stub:
    """Configurable ModelProvider stub for chat + chat_stream."""

    def __init__(
        self,
        descriptor: CapabilityDescriptor,
        *,
        chat_response: ChatResponse | None = None,
        error: BaseException | None = None,
        deltas: list[ChatStreamDelta] | None = None,
        done: ChatStreamDone | None = None,
        error_after_deltas: int | None = None,
    ) -> None:
        self._descriptor = descriptor
        self._chat_response = chat_response
        self._error = error
        self._deltas = deltas or []
        self._done = done
        self._error_after = error_after_deltas
        self.chat_calls = 0
        self.stream_calls = 0

    def capability(self) -> CapabilityDescriptor:
        return self._descriptor

    async def chat(self, request: ChatRequest) -> ChatResponse:  # noqa: ARG002
        self.chat_calls += 1
        if self._chat_response is None and self._error is not None:
            raise self._error
        assert self._chat_response is not None
        return self._chat_response

    def stream(self, request: ChatRequest) -> Any:  # noqa: ARG002 — protocol surface
        raise NotImplementedError

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:  # noqa: ARG002
        self.stream_calls += 1
        emitted = 0
        for delta in self._deltas:
            if self._error_after is not None and emitted >= self._error_after and self._error:
                raise self._error
            yield delta
            emitted += 1
        if self._error_after is not None and emitted >= self._error_after and self._error:
            raise self._error
        if self._done is not None:
            yield self._done


def _req() -> ChatRequest:
    return ChatRequest(messages=[Message(role=MessageRole.user, content="hi")])


def test_failover_requires_two_providers() -> None:
    with pytest.raises(ValueError, match="at least two providers"):
        FailoverProvider(providers=[_Stub(_desc())])


def test_conservative_capability_takes_the_floor() -> None:
    primary = _Stub(
        _desc(model_id="cloud", provider="openrouter", steps=15, strategy=AgentStrategy.frontier)
    )
    fallback = _Stub(
        _desc(
            model_id="qwen3:8b",
            provider="ollama",
            steps=8,
            strategy=AgentStrategy.guided,
            tier=ReasoningTier.mid,
            so=StructuredOutput.prompt_coaxed,
            ctx=131_072,
        )
    )
    chain = FailoverProvider(providers=[primary, fallback])
    cap = chain.capability()
    # Identity from the primary; behaviour bounded by the weakest link.
    assert cap.model_id == "cloud"
    assert cap.provider == "openrouter"
    assert cap.max_safe_autonomous_steps == 8  # min(15, 8)
    assert cap.recommended_strategy == AgentStrategy.guided  # least autonomous
    assert cap.reasoning_tier == ReasoningTier.mid
    assert cap.structured_output == StructuredOutput.prompt_coaxed
    assert cap.context_window == 131_072  # min


@pytest.mark.asyncio
async def test_chat_falls_back_on_primary_error() -> None:
    primary = _Stub(_desc(), error=ModelProviderRateLimitError("429 capped"))
    fallback = _Stub(_desc(model_id="local"), chat_response=_resp("from fallback", "local"))
    chain = FailoverProvider(providers=[primary, fallback])

    response = await chain.chat(_req())

    assert response.content == "from fallback"
    assert primary.chat_calls == 1
    assert fallback.chat_calls == 1


@pytest.mark.asyncio
async def test_circuit_breaker_skips_tripped_primary_on_next_call() -> None:
    primary = _Stub(_desc(), error=ModelProviderRateLimitError("429 capped"))
    fallback = _Stub(_desc(model_id="local"), chat_response=_resp("from fallback", "local"))
    chain = FailoverProvider(providers=[primary, fallback])

    await chain.chat(_req())  # trips the primary
    await chain.chat(_req())  # should skip the primary entirely

    assert primary.chat_calls == 1  # NOT called again
    assert fallback.chat_calls == 2


@pytest.mark.asyncio
async def test_chat_stream_falls_back_before_first_delta() -> None:
    primary = _Stub(
        _desc(), deltas=[], error=ModelProviderRateLimitError("429"), error_after_deltas=0
    )
    fallback = _Stub(
        _desc(model_id="local"),
        deltas=[ChatStreamDelta(content_delta="hello ")],
        done=ChatStreamDone(response=_resp("hello world", "local")),
    )
    chain = FailoverProvider(providers=[primary, fallback])

    events = [event async for event in chain.chat_stream(_req())]

    deltas = [e for e in events if isinstance(e, ChatStreamDelta)]
    dones = [e for e in events if isinstance(e, ChatStreamDone)]
    assert [d.content_delta for d in deltas] == ["hello "]
    assert dones and dones[0].response.content == "hello world"
    assert primary.stream_calls == 1
    assert fallback.stream_calls == 1


@pytest.mark.asyncio
async def test_chat_stream_reraises_on_mid_stream_error() -> None:
    """A failure AFTER a delta has been emitted can't be cleanly restarted on
    another link — it must re-raise (the loop's _fail_gracefully settles it)."""
    primary = _Stub(
        _desc(),
        deltas=[ChatStreamDelta(content_delta="partial")],
        error=RuntimeError("dropped mid-stream"),
        error_after_deltas=1,
    )
    fallback = _Stub(
        _desc(model_id="local"), done=ChatStreamDone(response=_resp("unused", "local"))
    )
    chain = FailoverProvider(providers=[primary, fallback])

    collected: list[str] = []
    with pytest.raises(RuntimeError, match="dropped mid-stream"):
        async for event in chain.chat_stream(_req()):
            if isinstance(event, ChatStreamDelta):
                collected.append(event.content_delta)

    assert collected == ["partial"]  # the delta reached the consumer
    assert fallback.stream_calls == 0  # NOT failed over after streaming started


@pytest.mark.asyncio
async def test_all_links_failed_raises_last_error() -> None:
    primary = _Stub(_desc(), error=ModelProviderRateLimitError("429"))
    fallback = _Stub(_desc(model_id="local"), error=RuntimeError("ollama down"))
    chain = FailoverProvider(providers=[primary, fallback])

    with pytest.raises(RuntimeError, match="ollama down"):
        await chain.chat(_req())


@pytest.mark.asyncio
async def test_cancelled_error_is_not_swallowed() -> None:
    """The Stop button cancels the task; CancelledError must propagate, never be
    treated as a provider failure that triggers a fallback call."""
    primary = _Stub(_desc(), error=asyncio.CancelledError())
    fallback = _Stub(_desc(model_id="local"), chat_response=_resp("should not run", "local"))
    chain = FailoverProvider(providers=[primary, fallback])

    with pytest.raises(asyncio.CancelledError):
        await chain.chat(_req())
    assert fallback.chat_calls == 0
