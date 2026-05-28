"""Tests for the relative/ISO time-field parser.

The load-bearing property: the natural relative forms a small model emits
(``now-6mo``, ``now-1y``, ``now-7d`` …) all resolve to a tz-aware datetime,
and the minutes/months ambiguity (``m`` vs ``M``) is resolved correctly.

Regression for Slice 5.0a: raising the query window to a year exposed that
the parser could not express months or years, so ``now-6mo`` / ``now-1y``
failed validation and the tool errored (then the model fabricated an answer).
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.tools.timefmt import default_time_to, parse_time_field


def _approx_days_ago(value: object, days: float, tol_seconds: float = 5.0) -> bool:
    assert isinstance(value, datetime)
    expected = datetime.now(UTC) - timedelta(days=days)
    return abs((value - expected).total_seconds()) <= tol_seconds


@pytest.mark.parametrize(
    ("expr", "days"),
    [
        ("now-7d", 7),
        ("now-30d", 30),
        ("now-3w", 21),
        ("now-6mo", 180),       # the screenshot failure
        ("now-1y", 365),        # the screenshot failure
        ("now-2months", 60),
        ("now-1month", 30),
        ("now-12mo", 360),
        ("now-2y", 730),
        ("now - 6 mo", 180),    # whitespace tolerated
        ("NOW-1Y", 365),        # case tolerated for the 'now' and unit word
    ],
)
def test_relative_day_scale_units_parse(expr: str, days: float) -> None:
    assert _approx_days_ago(parse_time_field(expr), days)


def test_minutes_vs_months_case_sensitivity() -> None:
    """'m' is minutes; 'M' is months — case must not be flattened."""
    minutes = parse_time_field("now-15m")
    assert isinstance(minutes, datetime)
    assert abs((datetime.now(UTC) - minutes).total_seconds() - 15 * 60) <= 5

    months = parse_time_field("now-3M")  # months, NOT 3 minutes
    assert _approx_days_ago(months, 90)


def test_bare_now_is_current_time() -> None:
    v = parse_time_field("now")
    assert isinstance(v, datetime)
    assert abs((datetime.now(UTC) - v).total_seconds()) <= 5


def test_iso_8601_still_parses_and_is_tz_aware() -> None:
    v = parse_time_field("2026-05-21T10:00:00Z")
    assert isinstance(v, datetime)
    assert v.tzinfo is not None
    assert v.year == 2026 and v.month == 5 and v.day == 21


def test_unknown_unit_passes_through_for_pydantic_to_reject() -> None:
    """An unrecognized unit must NOT be silently guessed — return as-is."""
    assert parse_time_field("now-5fortnights") == "now-5fortnights"


def test_naive_datetime_gets_utc() -> None:
    v = parse_time_field(datetime(2026, 1, 1, 12, 0, 0))  # noqa: DTZ001 — naive on purpose
    assert isinstance(v, datetime)
    assert v.tzinfo is UTC


def test_default_time_to_is_now_utc() -> None:
    v = default_time_to()
    assert v.tzinfo is not None
    assert abs((datetime.now(UTC) - v).total_seconds()) <= 5
