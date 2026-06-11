"""Tests for resource guardrails — time range, result count, rate limit."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from wolf_server.guardrails.limits import (
    GuardrailViolation,
    ResourceLimits,
    enforce_limits,
    truncate_for_context,
)
from wolf_server.guardrails.rate_limit import OrganizationRateLimiter

# ─── enforce_limits ──────────────────────────────────────────────────────────


def test_enforce_limits_passes_with_valid_window() -> None:
    end = datetime.now(UTC)
    enforce_limits(time_from=end - timedelta(days=7), time_to=end, requested_size=100)


def test_enforce_limits_rejects_time_window_exceeding_max() -> None:
    end = datetime.now(UTC)
    with pytest.raises(GuardrailViolation, match="maximum"):
        enforce_limits(time_from=end - timedelta(days=400), time_to=end)


def test_enforce_limits_allows_full_year_raw_search() -> None:
    """The default ceiling is a generous 365 days (Slice 5.0a)."""
    end = datetime.now(UTC)
    enforce_limits(time_from=end - timedelta(days=365), time_to=end)  # no raise


def test_enforce_limits_skips_window_cap_for_aggregations() -> None:
    """enforce_time_window=False lets bucket-bounded tools span any range."""
    end = datetime.now(UTC)
    enforce_limits(
        time_from=end - timedelta(days=900),
        time_to=end,
        enforce_time_window=False,
    )  # must NOT raise


def test_enforce_limits_still_checks_inverted_window_when_cap_skipped() -> None:
    """Exempting the width cap must NOT exempt the correctness check."""
    end = datetime.now(UTC)
    with pytest.raises(GuardrailViolation, match="before"):
        enforce_limits(
            time_from=end,
            time_to=end - timedelta(hours=1),
            enforce_time_window=False,
        )


def test_enforce_limits_allows_max_plus_clock_drift() -> None:
    """A 'now-365d' .. 'now' query parses the two nows microseconds apart.

    Regression for the Slice 5.0a guardrail bug: the span lands at
    max + a few µs and a strict `>` rejected an obviously-in-range query.
    The 1-second grace must absorb sub-second drift.
    """
    end = datetime.now(UTC)
    drift = end - timedelta(days=365) - timedelta(microseconds=6)
    enforce_limits(time_from=drift, time_to=end)  # must NOT raise


def test_enforce_limits_still_rejects_beyond_grace() -> None:
    """The grace is sub-second slack, not a meaningful widening."""
    end = datetime.now(UTC)
    over = end - timedelta(days=365) - timedelta(seconds=5)
    with pytest.raises(GuardrailViolation, match="maximum"):
        enforce_limits(time_from=over, time_to=end)


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
        enforce_limits(time_from=end - timedelta(hours=2), time_to=end, limits=tight)


# ─── truncate_for_context ────────────────────────────────────────────────────


def test_truncate_returns_payload_unchanged_under_limit() -> None:
    payload = "a" * 100
    assert truncate_for_context(payload) == payload


def test_truncate_appends_marker_when_over_limit() -> None:
    tight = ResourceLimits(max_context_bytes=10)
    out = truncate_for_context("a" * 1000, limits=tight)
    assert "truncated" in out
    assert len(out) > 10  # marker added


# ─── OrganizationRateLimiter ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limiter_allows_within_burst() -> None:
    limiter = OrganizationRateLimiter(rate_per_minute=60, burst=10)
    organization = uuid.uuid4()
    for _ in range(10):
        await limiter.take(organization)


@pytest.mark.asyncio
async def test_rate_limiter_rejects_when_bucket_exhausted() -> None:
    limiter = OrganizationRateLimiter(rate_per_minute=60, burst=3)
    organization = uuid.uuid4()
    for _ in range(3):
        await limiter.take(organization)
    with pytest.raises(GuardrailViolation, match="Rate limit"):
        await limiter.take(organization)


@pytest.mark.asyncio
async def test_rate_limiter_buckets_are_per_organization() -> None:
    limiter = OrganizationRateLimiter(rate_per_minute=60, burst=2)
    organization_a = uuid.uuid4()
    organization_b = uuid.uuid4()
    for _ in range(2):
        await limiter.take(organization_a)
        await limiter.take(organization_b)
    with pytest.raises(GuardrailViolation):
        await limiter.take(organization_a)
    # Organization B is unaffected by Organization A's exhaustion.
    with pytest.raises(GuardrailViolation):
        await limiter.take(organization_b)


@pytest.mark.asyncio
async def test_rate_limiter_refills_over_time() -> None:
    # High rate so refill happens within test timeframes.
    limiter = OrganizationRateLimiter(rate_per_minute=3000, burst=1)  # ~50/sec
    organization = uuid.uuid4()
    await limiter.take(organization)
    with pytest.raises(GuardrailViolation):
        await limiter.take(organization)
    await asyncio.sleep(0.1)  # > 1/50 second
    await limiter.take(organization)  # should succeed after refill
