"""Provider-level failover chain (operator/per-org resilience).

A :class:`FailoverProvider` composes an ordered chain of ``ModelProvider``s —
``[primary, fallback, ...]`` — and transparently continues on the next link
when the current one fails *for any reason* (rate-limit / quota, timeout,
5xx, malformed-request, provider outage).  The analyst sees one seamless
model: if the primary can't answer, the fallback does, and the stream never
breaks.

The intended posture (operator decision 2026-07-01):

  * Wolf's **default** primary is local Ollama — reliable, uncapped, private —
    so the default ships with **no chain** (nothing to fail over *to*).
  * An organisation may configure a hosted model (e.g. OpenRouter) as *its*
    primary; the chain then makes **local Ollama the automatic safety net**
    for that org, so a capped/erroring cloud model never leaves the analyst
    without an answer.

This is deliberately distinct from :mod:`wolf_server.models.fallback` — that
module is the *structured-output* fallback (prompt-mode tool-calling for
models without native tool-calling).  This one is *provider* failover.

Design notes:
  * **Clean streaming failover.** The OpenAI/Ollama adapters raise on a
    ``>= 400`` status *before* yielding the first ``ChatStreamDelta`` (the
    status check runs right after the stream opens), so a 429/400/timeout
    fails over *before any token reaches the client*.  If a link fails *after*
    it has already emitted a delta (a mid-stream network drop), we re-raise —
    a half-streamed answer can't be cleanly restarted; the agent loop's
    ``_fail_gracefully`` then settles the UI honestly.
  * **``CancelledError`` propagates.** We catch ``Exception``, not
    ``BaseException``, so the analyst's Stop button (task cancellation) is
    never swallowed as a "failure" that triggers a fallback call.
  * **Per-instance circuit-breaker.** A provider that fails is skipped for the
    rest of *this* chain instance (one chat request = up to N loop steps), so
    a capped primary is probed **once** per query, not once per step.  A fresh
    instance per request means the primary is re-probed on the next query —
    the moment its quota resets, Wolf uses it again (no stale lockout).
  * **Conservative capability floor.** :meth:`capability` reports the *safest*
    profile across the chain — ``min`` step budget and the least-autonomous
    strategy — because any given call may land on the weakest link.  This
    keeps the loop from over-driving a smaller fallback model (the exact
    ``budget_exhausted`` runaway seen on 2026-07-01).
"""

import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

import structlog
from wolf_common.errors import WolfError
from wolf_schema import CapabilityDescriptor, ChatRequest, ChatResponse
from wolf_schema.capability import (
    AgentStrategy,
    NativeToolCalling,
    ReasoningTier,
    StructuredOutput,
)

from wolf_server.models.interface import (
    ChatStreamDone,
    ChatStreamEvent,
    ModelProvider,
)

logger = structlog.get_logger(__name__)

# How long a failed link is skipped on the fast path (seconds).  Chains are
# built per-request, so this mostly governs skipping within one query's loop
# steps; a generous value keeps a link tripped for the life of the instance.
_DEFAULT_COOLOFF_SECONDS = 120.0

# Ordinal rank of each grading enum, lowest = most conservative.  The chain's
# reported capability takes the MIN across links (the weakest link you might
# hit), so the loop is bounded by the safest profile.
_TOOL_RANK: dict[NativeToolCalling, int] = {
    NativeToolCalling.none: 0,
    NativeToolCalling.partial: 1,
    NativeToolCalling.full: 2,
}
_TIER_RANK: dict[ReasoningTier, int] = {
    ReasoningTier.basic: 0,
    ReasoningTier.mid: 1,
    ReasoningTier.strong: 2,
    ReasoningTier.frontier: 3,
}
_SO_RANK: dict[StructuredOutput, int] = {
    StructuredOutput.unreliable: 0,
    StructuredOutput.prompt_coaxed: 1,
    StructuredOutput.schema_enforced: 2,
}
_STRAT_RANK: dict[AgentStrategy, int] = {
    AgentStrategy.pipeline: 0,
    AgentStrategy.guided: 1,
    AgentStrategy.frontier: 2,
}


def _min_by_rank[T](values: list[T], rank: dict[T, int]) -> T:
    """Return the value with the lowest rank (most conservative)."""
    return min(values, key=lambda v: rank[v])


def _conservative_capability(links: list[CapabilityDescriptor]) -> CapabilityDescriptor:
    """Merge chain capabilities into the safest common profile.

    Identity fields (``model_id`` / ``provider`` / ``license_class``) come from
    the primary (first link) for reporting; behavioural fields take the
    conservative floor so the loop never over-drives a weaker fallback.
    """
    primary = links[0]
    return CapabilityDescriptor(
        model_id=primary.model_id,
        provider=primary.provider,
        context_window=min(link.context_window for link in links),
        native_tool_calling=_min_by_rank([link.native_tool_calling for link in links], _TOOL_RANK),
        reasoning_tier=_min_by_rank([link.reasoning_tier for link in links], _TIER_RANK),
        structured_output=_min_by_rank([link.structured_output for link in links], _SO_RANK),
        max_safe_autonomous_steps=min(link.max_safe_autonomous_steps for link in links),
        recommended_strategy=_min_by_rank(
            [link.recommended_strategy for link in links], _STRAT_RANK
        ),
        license_class=primary.license_class,
    )


@dataclass
class _Link:
    provider: ModelProvider
    tripped_until: float = 0.0  # monotonic seconds; 0 = healthy


@dataclass
class FailoverProvider:
    """A ``ModelProvider`` that tries an ordered chain of providers in turn.

    Construct with two or more providers; the first is the primary.  On any
    non-cancellation failure of a link (before it streams a token), the next
    link is tried and the failed one is circuit-broken for this instance.
    """

    providers: list[ModelProvider]
    cooloff_seconds: float = _DEFAULT_COOLOFF_SECONDS
    _clock: Callable[[], float] = time.monotonic
    _links: list[_Link] = field(init=False)
    _capability: CapabilityDescriptor = field(init=False)

    def __post_init__(self) -> None:
        if len(self.providers) < 2:
            raise ValueError("FailoverProvider needs at least two providers (primary + fallback)")
        self._links = [_Link(provider=p) for p in self.providers]
        self._capability = _conservative_capability([p.capability() for p in self.providers])

    # ── ModelProvider protocol ────────────────────────────────────────────

    def capability(self) -> CapabilityDescriptor:
        return self._capability

    def effective_context_window(self) -> int:
        """Conservative floor across the chain — any link may end up serving
        the request, so the loop's context-fit guard (6-f.5) must fit the
        smallest effective window in the chain (same philosophy as
        :func:`_conservative_capability`)."""
        windows: list[int] = []
        for provider in self.providers:
            fn = getattr(provider, "effective_context_window", None)
            if callable(fn):
                windows.append(int(fn()))
            else:
                windows.append(int(provider.capability().context_window))
        return min(windows)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        errors: list[Exception] = []
        for link in self._order():
            try:
                return await link.provider.chat(request)
            except Exception as exc:  # noqa: BLE001 — any provider failure → next link
                self._trip(link, exc)
                errors.append(exc)
        raise self._all_failed(errors)

    def stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """Legacy single-chunk protocol path (progressive path is chat_stream)."""

        async def _gen() -> AsyncIterator[str]:
            response = await self.chat(request)
            yield response.content

        return _gen()

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        """Stream from the first healthy link; fail over on a pre-stream error.

        If a link raises *after* emitting a delta (mid-stream drop), re-raise —
        a partially-streamed answer can't be cleanly restarted on another link.
        """
        errors: list[Exception] = []
        for link in self._order():
            emitted = False
            try:
                async for event in _events(link.provider, request):
                    emitted = True
                    yield event
                return
            except Exception as exc:  # noqa: BLE001 — any provider failure → next link
                if emitted:
                    raise
                self._trip(link, exc)
                errors.append(exc)
        raise self._all_failed(errors)

    # ── internals ─────────────────────────────────────────────────────────

    def _order(self) -> list[_Link]:
        """Healthy links first (in chain order); if all are tripped, try them
        all anyway as a last resort rather than refusing to answer."""
        now = self._clock()
        healthy = [link for link in self._links if now >= link.tripped_until]
        return healthy or list(self._links)

    def _trip(self, link: _Link, exc: Exception) -> None:
        link.tripped_until = self._clock() + self.cooloff_seconds
        cap = link.provider.capability()
        # Surface the normalized quota state (429/402 → free_daily_cap /
        # credits_exhausted / rate_limited + live remaining/reset) when the
        # error carries one, so a degraded-to-fallback query is auditable and
        # the per-org phase's "answered on local Ollama, resets in Nh" chip has
        # a ready signal. getattr keeps this quota-type-agnostic.
        quota = getattr(exc, "quota", None)
        log_fields: dict[str, object] = {
            "failed_provider": cap.provider,
            "failed_model": cap.model_id,
            "error_type": type(exc).__name__,
            "error": str(exc)[:200],
        }
        if quota is not None:
            log_fields["quota_kind"] = getattr(getattr(quota, "kind", None), "value", None)
            log_fields["quota_remaining"] = quota.remaining
            log_fields["quota_limit"] = quota.limit
        logger.warning("model_failover_link_failed", **log_fields)

    def _all_failed(self, errors: list[Exception]) -> Exception:
        logger.error("model_failover_exhausted", error_count=len(errors))
        if errors:
            return errors[-1]
        return WolfError("Failover chain produced no provider to call")


async def _events(provider: ModelProvider, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
    """Yield a provider's stream events, adapting a blocking-only provider
    (no ``chat_stream``, e.g. Anthropic) into a single terminal done event."""
    stream_fn = getattr(provider, "chat_stream", None)
    if stream_fn is None:
        yield ChatStreamDone(response=await provider.chat(request))
        return
    async for event in stream_fn(request):
        yield event
