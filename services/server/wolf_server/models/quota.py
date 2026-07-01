"""Normalized provider-quota state — the *contract* for reading a hosted
provider's rate-limit / credit exhaustion, so Wolf can prompt an analyst
actionably instead of dumping raw provider JSON (2026-07-01).

Design note — hardcode the CONTRACT, never the NUMBERS. The daily cap, the
remaining count, and the reset time are all account-state-dependent and mutable
(OpenRouter's free cap flips from 50/day to 1000/day once ≥$10 of credits are
bought, and the numbers are placeholders even in their own docs). So Wolf reads
them LIVE from the provider's own signals and only hardcodes *how to interpret*
those signals:

  - the ``X-RateLimit-{Limit,Remaining,Reset}`` response headers OpenRouter
    stamps on a 429 (``Reset`` is a Unix epoch in MILLISECONDS), and
  - the error body: ``free-models-per-day`` (the shared free-tier daily cap)
    vs a per-model "temporarily rate-limited upstream", vs a 402 that means a
    negative credit balance.

This is the same stable-contract-over-live-state posture Wolf already keeps for
the Wazuh active-response API and the ``KNOWN_MODELS`` registry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class QuotaKind(StrEnum):
    """Why a hosted provider refused the call — drives the user-facing hint."""

    #: 429 whose body names the shared free-tier daily request cap.
    free_daily_cap = "free_daily_cap"
    #: 429 without the free-cap marker — a transient per-model / upstream limit.
    rate_limited = "rate_limited"
    #: 402 Payment Required — a negative credit balance.
    credits_exhausted = "credits_exhausted"


def _parse_reset(raw: str | None) -> datetime | None:
    """Parse an ``X-RateLimit-Reset`` value into an aware UTC datetime.

    OpenRouter stamps this as a Unix epoch in **milliseconds**; some providers
    use seconds. Disambiguate by magnitude (ms ≈ 1.7e12, s ≈ 1.7e9 in this
    era) so either is handled. Returns None on anything unparseable rather than
    raising — a missing reset time must never break error surfacing.
    """
    if not raw:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    # > 1e12 → milliseconds; else seconds.
    seconds = value / 1000.0 if value > 1e12 else value
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _humanize_until(reset_at: datetime | None, *, now: datetime | None = None) -> str:
    """Render a compact 'in 3h 20m' / 'in 45s' phrase, or '' if unknown/past."""
    if reset_at is None:
        return ""
    current = now or datetime.now(tz=UTC)
    delta = (reset_at - current).total_seconds()
    if delta <= 0:
        return ""
    minutes, _ = divmod(int(delta), 60)
    hours, mins = divmod(minutes, 60)
    if hours:
        return f"in {hours}h {mins}m" if mins else f"in {hours}h"
    if mins:
        return f"in {mins}m"
    return f"in {int(delta)}s"


@dataclass(frozen=True)
class ProviderQuota:
    """A hosted provider's exhaustion state, normalized across providers.

    Every numeric field is optional: providers vary in what they expose, and a
    missing value must degrade to a still-useful message, never a crash.
    """

    provider: str
    kind: QuotaKind
    limit: int | None = None
    remaining: int | None = None
    reset_at: datetime | None = None
    is_free_tier: bool | None = None
    detail: str = ""

    def _counts(self) -> str:
        if self.limit is not None and self.remaining is not None:
            return f" ({self.remaining} of {self.limit} left)"
        return ""

    def _reset(self, *, now: datetime | None = None) -> str:
        phrase = _humanize_until(self.reset_at, now=now)
        return f"; resets {phrase}" if phrase else ""

    def user_message(self, *, now: datetime | None = None) -> str:
        """A short, actionable statement of the exhaustion + the way out.

        Framing (fatal answer vs. failover chip) is the caller's job; this is
        the core fact + remedy, safe to embed in either.
        """
        name = self.provider or "the model provider"
        if self.kind is QuotaKind.credits_exhausted:
            return (
                f"{name} credits are exhausted (payment required){self._counts()}. "
                "Add credits to continue, or switch this organization back to a "
                "local Ollama model."
            )
        if self.kind is QuotaKind.free_daily_cap:
            return (
                f"{name}'s free-tier daily request cap is reached"
                f"{self._counts()}{self._reset(now=now)}. Add credits to raise the "
                "cap, wait for the reset, or use a local Ollama model."
            )
        return (
            f"{name} is temporarily rate-limited{self._counts()}{self._reset(now=now)}. "
            "Retry in a moment, or use a local Ollama model."
        )


def _body_names_free_cap(body_text: str) -> bool:
    """True when a 429 body identifies the shared free-tier daily cap.

    Checks the raw text (cheap, resilient to JSON shape drift) for OpenRouter's
    ``free-models-per-day`` marker / its "free model requests per day" phrasing.
    """
    lowered = body_text.lower()
    return "free-models-per-day" in lowered or "free model requests per day" in lowered


def quota_from_response(
    *,
    provider: str,
    status_code: int,
    headers: Mapping[str, str] | None,
    body_text: str,
) -> ProviderQuota | None:
    """Classify a hosted provider's 429/402 into a :class:`ProviderQuota`.

    Returns None for statuses that are not quota/credit exhaustion (the caller
    keeps its generic provider-error path for those). Never raises — a parse
    problem yields a coarser but still-useful quota, because this runs on the
    error path where robustness matters most.
    """
    if status_code not in (402, 429):
        return None
    hdrs = headers or {}
    # httpx headers are case-insensitive Mappings; a plain dict passed in a test
    # may not be, so look the keys up defensively.
    limit = _parse_int(_header(hdrs, "x-ratelimit-limit"))
    remaining = _parse_int(_header(hdrs, "x-ratelimit-remaining"))
    reset_at = _parse_reset(_header(hdrs, "x-ratelimit-reset"))

    if status_code == 402:
        kind = QuotaKind.credits_exhausted
    elif _body_names_free_cap(body_text):
        kind = QuotaKind.free_daily_cap
    else:
        kind = QuotaKind.rate_limited

    return ProviderQuota(
        provider=provider,
        kind=kind,
        limit=limit,
        remaining=remaining,
        reset_at=reset_at,
        detail=_error_message(body_text),
    )


def _header(headers: Mapping[str, str], key: str) -> str | None:
    """Case-insensitive header lookup (``key`` must be lowercase).

    Works for both a plain ``dict`` (arbitrary key case) and ``httpx.Headers``
    (already lowercased) by comparing lowercased keys.
    """
    for existing, val in headers.items():
        if existing.lower() == key:
            return val
    return None


def _error_message(body_text: str) -> str:
    """Best-effort extraction of the provider's error message for logs/audit."""
    try:
        parsed = json.loads(body_text)
    except (json.JSONDecodeError, TypeError):
        return body_text[:200]
    if isinstance(parsed, dict):
        err = parsed.get("error")
        if isinstance(err, dict):
            message = err.get("message")
            if isinstance(message, str):
                return message[:200]
        if isinstance(err, str):
            return err[:200]
    return body_text[:200]
