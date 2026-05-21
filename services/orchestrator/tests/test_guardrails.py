"""Tests for resource guardrails — time range, result count, rate limit."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.guardrails.limits import (
    GuardrailViolation,
    ResourceLimits,
    enforce_limits,
    truncate_for_context,
)
from app.guardrails.rate_limit import TenantRateLimiter

# ─── enforce_limits ──────────────────────────────────────────────────────────


def test_enforce_limits_passes_with_valid_window() -> None:
    end = datetime.now(UTC)
    enforce_limits(time_from=end - timedelta(days=7), time_to=end, requested_size=100)


def test_enforce_limits_rejects_time_window_exceeding_max() -> None:
    end = datetime.now(UTC)
    with pytest.raises(GuardrailViolation, match="maximum"):
        enforce_limits(time_from=end - timedelta(days=90), time_to=end)


def test_enforce_limits_rejects_inverted_time_window() -> None:
    end = datetime.now(UTC)
    with pytest.raises(GuardrailViolation, match="before"):
        enforce_limits(time_from=end, time_to=end - timedelta(hours=1))


def test_enforce_limits_rejects_oversized_result_request() -> None:
    with pytest.raises(GuardrailViolation, match="paginate"):
        enforce_limits(requested_size=5000)


def test_enforce_limits_respects_custom_limits() -> None:
    tight = ResourceLimits(max_time_range=timedelta(hours=1), max_results_per_query=10)
    end = datetime.now(UTC)
    with pytest.raises(GuardrailViolation):
        enforce_limits(
            time_from=end - timedelta(hours=2), time_to=end, limits=tight
        )


# ─── truncate_for_context ────────────────────────────────────────────────────


def test_truncate_returns_payload_unchanged_under_limit() -> None:
    payload = "a" * 100
    assert truncate_for_context(payload) == payload


def test_truncate_appends_marker_when_over_limit() -> None:
    tight = ResourceLimits(max_context_bytes=10)
    out = truncate_for_context("a" * 1000, limits=tight)
    assert "truncated" in out
    assert len(out) > 10  # marker added


# ─── TenantRateLimiter ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limiter_allows_within_burst() -> None:
    limiter = TenantRateLimiter(rate_per_minute=60, burst=10)
    tenant = uuid.uuid4()
    for _ in range(10):
        await limiter.take(tenant)


@pytest.mark.asyncio
async def test_rate_limiter_rejects_when_bucket_exhausted() -> None:
    limiter = TenantRateLimiter(rate_per_minute=60, burst=3)
    tenant = uuid.uuid4()
    for _ in range(3):
        await limiter.take(tenant)
    with pytest.raises(GuardrailViolation, match="Rate limit"):
        await limiter.take(tenant)


@pytest.mark.asyncio
async def test_rate_limiter_buckets_are_per_tenant() -> None:
    limiter = TenantRateLimiter(rate_per_minute=60, burst=2)
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    for _ in range(2):
        await limiter.take(tenant_a)
        await limiter.take(tenant_b)
    with pytest.raises(GuardrailViolation):
        await limiter.take(tenant_a)
    # Tenant B is unaffected by Tenant A's exhaustion.
    with pytest.raises(GuardrailViolation):
        await limiter.take(tenant_b)


@pytest.mark.asyncio
async def test_rate_limiter_refills_over_time() -> None:
    # High rate so refill happens within test timeframes.
    limiter = TenantRateLimiter(rate_per_minute=3000, burst=1)  # ~50/sec
    tenant = uuid.uuid4()
    await limiter.take(tenant)
    with pytest.raises(GuardrailViolation):
        await limiter.take(tenant)
    await asyncio.sleep(0.1)  # > 1/50 second
    await limiter.take(tenant)  # should succeed after refill
