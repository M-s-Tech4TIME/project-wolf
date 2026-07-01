"""Reactive provider-quota surfacing (2026-07-01).

Pins the CONTRACT for reading a hosted provider's exhaustion from the live
signals it stamps on a 429/402 — the ``X-RateLimit-*`` headers + the error
body — and turning that into an actionable analyst message. The numbers stay
live; only the interpretation is hardcoded, so these tests assert the
classification + the message shape, never a specific cap value.
"""

from datetime import UTC, datetime

import httpx
import pytest
from wolf_schema import ChatRequest
from wolf_schema.chat import Message, MessageRole
from wolf_server.models.openai import (
    ModelProviderPaymentRequiredError,
    ModelProviderRateLimitError,
    _provider_error,
)
from wolf_server.models.openrouter import OPENROUTER_BASE_URL, OpenRouterAdapter
from wolf_server.models.quota import (
    ProviderQuota,
    QuotaKind,
    _parse_reset,
    quota_from_response,
)

# A fixed "now" so reset-time phrasing is deterministic.
_NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
# 12:00 + 3h20m, expressed as an epoch in MILLISECONDS (OpenRouter's format).
_RESET_MS = str(int(datetime(2026, 7, 1, 15, 20, 0, tzinfo=UTC).timestamp() * 1000))

_FREE_CAP_BODY = (
    '{"error":{"code":429,"message":"Rate limit exceeded: free-models-per-day. '
    'Add 10 credits to unlock 1000 free model requests per day"}}'
)


# ── classification ────────────────────────────────────────────────────────


def test_free_daily_cap_classified_with_live_counts_and_reset() -> None:
    quota = quota_from_response(
        provider="OpenRouter",
        status_code=429,
        headers={
            "X-RateLimit-Limit": "50",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": _RESET_MS,
        },
        body_text=_FREE_CAP_BODY,
    )
    assert quota is not None
    assert quota.kind is QuotaKind.free_daily_cap
    assert quota.limit == 50
    assert quota.remaining == 0
    assert quota.reset_at == datetime(2026, 7, 1, 15, 20, 0, tzinfo=UTC)
    msg = quota.user_message(now=_NOW)
    # Live numbers surface; the remedy is actionable; the reset is humanized.
    assert "0 of 50 left" in msg
    assert "resets in 3h 20m" in msg
    assert "credits" in msg.lower()


def test_generic_429_is_transient_rate_limited_not_free_cap() -> None:
    quota = quota_from_response(
        provider="OpenRouter",
        status_code=429,
        headers={},
        body_text='{"error":{"message":"Provider temporarily rate-limited upstream"}}',
    )
    assert quota is not None
    assert quota.kind is QuotaKind.rate_limited
    assert "rate-limited" in quota.user_message(now=_NOW)


def test_402_is_credits_exhausted() -> None:
    quota = quota_from_response(
        provider="OpenRouter",
        status_code=402,
        headers={},
        body_text='{"error":{"code":402,"message":"Insufficient credits"}}',
    )
    assert quota is not None
    assert quota.kind is QuotaKind.credits_exhausted
    assert "credits are exhausted" in quota.user_message(now=_NOW)


def test_non_quota_status_returns_none() -> None:
    # A 500 is a provider error, not a quota signal — the caller keeps its
    # generic error path.
    assert quota_from_response(
        provider="OpenRouter", status_code=500, headers={}, body_text="boom"
    ) is None


def test_missing_counts_still_produce_a_usable_message() -> None:
    quota = quota_from_response(
        provider="OpenRouter", status_code=429, headers={}, body_text=_FREE_CAP_BODY
    )
    assert quota is not None
    msg = quota.user_message(now=_NOW)
    # No counts / reset available, but the remedy is still present and there is
    # no dangling "( of )" fragment.
    assert "(" not in msg
    assert "credits" in msg.lower()


# ── reset parsing (ms vs s vs garbage) ────────────────────────────────────


def test_parse_reset_handles_milliseconds_seconds_and_garbage() -> None:
    ms = _parse_reset(_RESET_MS)
    secs = _parse_reset(str(int(datetime(2026, 7, 1, 15, 20, 0, tzinfo=UTC).timestamp())))
    assert ms == secs == datetime(2026, 7, 1, 15, 20, 0, tzinfo=UTC)
    assert _parse_reset(None) is None
    assert _parse_reset("not-a-number") is None
    assert _parse_reset("0") is None


def test_reset_in_the_past_is_omitted() -> None:
    past = ProviderQuota(
        provider="OpenRouter",
        kind=QuotaKind.free_daily_cap,
        reset_at=datetime(2026, 7, 1, 11, 0, 0, tzinfo=UTC),  # before _NOW
    )
    assert "resets" not in past.user_message(now=_NOW)


# ── _provider_error attaches the quota to the right error type ─────────────


def test_provider_error_429_attaches_quota() -> None:
    err = _provider_error(
        429,
        _FREE_CAP_BODY,
        provider="OpenRouter",
        headers={"X-RateLimit-Limit": "50", "X-RateLimit-Remaining": "0"},
    )
    assert isinstance(err, ModelProviderRateLimitError)
    assert err.quota is not None
    assert err.quota.kind is QuotaKind.free_daily_cap
    assert err.quota.remaining == 0


def test_provider_error_402_maps_to_payment_required() -> None:
    err = _provider_error(402, '{"error":{"message":"neg balance"}}', provider="OpenRouter")
    assert isinstance(err, ModelProviderPaymentRequiredError)
    assert err.quota is not None
    assert err.quota.kind is QuotaKind.credits_exhausted


# ── adapter integration: a real 429/402 response → enriched error ──────────


def _adapter(transport: httpx.MockTransport) -> OpenRouterAdapter:
    client = httpx.AsyncClient(base_url=OPENROUTER_BASE_URL, transport=transport, timeout=5.0)
    return OpenRouterAdapter(api_key="k", model_id="cohere/north-mini-code:free", client=client)


def _req() -> ChatRequest:
    return ChatRequest(messages=[Message(role=MessageRole.user, content="hi")], temperature=0.0)


@pytest.mark.asyncio
async def test_adapter_429_surfaces_quota_from_headers_and_body() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            text=_FREE_CAP_BODY,
            headers={
                "X-RateLimit-Limit": "50",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": _RESET_MS,
            },
        )

    adapter = _adapter(httpx.MockTransport(_handler))
    with pytest.raises(ModelProviderRateLimitError) as exc_info:
        await adapter.chat(_req())
    quota = exc_info.value.quota
    assert quota is not None
    assert quota.provider == "OpenRouter"
    assert quota.kind is QuotaKind.free_daily_cap
    assert quota.limit == 50 and quota.remaining == 0
    assert quota.reset_at is not None


@pytest.mark.asyncio
async def test_adapter_402_surfaces_credits_exhausted() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, text='{"error":{"code":402,"message":"neg balance"}}')

    adapter = _adapter(httpx.MockTransport(_handler))
    with pytest.raises(ModelProviderPaymentRequiredError) as exc_info:
        await adapter.chat(_req())
    assert exc_info.value.quota is not None
    assert exc_info.value.quota.kind is QuotaKind.credits_exhausted


# ── loop surfacing: the failure message becomes actionable ─────────────────


def test_loop_failure_message_uses_quota_when_present() -> None:
    from wolf_server.agent.loop import _model_failure_message

    quota = ProviderQuota(
        provider="OpenRouter",
        kind=QuotaKind.free_daily_cap,
        limit=50,
        remaining=0,
    )
    msg = _model_failure_message(ModelProviderRateLimitError("capped", quota=quota))
    assert "OpenRouter" in msg
    assert "0 of 50 left" in msg


def test_loop_failure_message_falls_back_without_quota() -> None:
    from wolf_server.agent.loop import _model_failure_message

    # No quota attached (e.g. a provider that stamps no headers) → the generic
    # but still-clear 429 message.
    msg = _model_failure_message(ModelProviderRateLimitError("capped"))
    assert "rate-limited" in msg
    assert "local model" in msg
