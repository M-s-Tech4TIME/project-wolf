"""Permissive time-field parser for tool inputs.

Models — especially small / local ones — naturally express time windows in
Wazuh-style relative syntax (``now-24h``, ``now-1d``) or as ISO datetime.
Forcing strict ISO causes them to fail at the schema-validation step
*before* the tool ever runs, which then dies as ``tool.call.schema_invalid``.

This module provides one function that accepts either form and returns a
timezone-aware ``datetime``.  Tool input models call it from a
``@field_validator(mode="before")`` on their time fields.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

# now  |  now-<n><unit>  — whitespace and unit case are tolerated; the unit
# is resolved by _resolve_unit so we can distinguish minutes from months.
_RELATIVE_BARE = re.compile(r"^now$", re.IGNORECASE)
_RELATIVE_DELTA = re.compile(r"^now\s*-\s*(\d+)\s*([a-zA-Z]+)$", re.IGNORECASE)

# Approximations for calendar units — exact enough for an alert query window
# and dependency-free. A "month" is 30 days; a "year" is 365 days.
_MONTH = timedelta(days=30)
_YEAR = timedelta(days=365)

# Multi-letter unit aliases (matched case-insensitively). The single letters
# 'm' and 'M' are handled separately in _resolve_unit because their case is
# load-bearing: 'm' is minutes, 'M' is months (Wazuh/OpenSearch convention).
_UNIT_ALIASES: dict[str, timedelta] = {
    "s": timedelta(seconds=1), "sec": timedelta(seconds=1),
    "secs": timedelta(seconds=1), "second": timedelta(seconds=1),
    "seconds": timedelta(seconds=1),
    "min": timedelta(minutes=1), "mins": timedelta(minutes=1),
    "minute": timedelta(minutes=1), "minutes": timedelta(minutes=1),
    "h": timedelta(hours=1), "hr": timedelta(hours=1), "hrs": timedelta(hours=1),
    "hour": timedelta(hours=1), "hours": timedelta(hours=1),
    "d": timedelta(days=1), "day": timedelta(days=1), "days": timedelta(days=1),
    "w": timedelta(weeks=1), "wk": timedelta(weeks=1), "wks": timedelta(weeks=1),
    "week": timedelta(weeks=1), "weeks": timedelta(weeks=1),
    "mo": _MONTH, "mon": _MONTH, "mos": _MONTH, "month": _MONTH, "months": _MONTH,
    "y": _YEAR, "yr": _YEAR, "yrs": _YEAR, "year": _YEAR, "years": _YEAR,
}


def _resolve_unit(unit: str) -> timedelta | None:
    """Map a relative-time unit token to a timedelta, or None if unknown.

    Single-letter 'm'/'M' is case-sensitive: lowercase is minutes, uppercase
    is months. Everything else is matched case-insensitively.
    """
    if unit == "m":
        return timedelta(minutes=1)
    if unit == "M":
        return _MONTH
    return _UNIT_ALIASES.get(unit.lower())


def parse_time_field(value: Any) -> datetime | Any:
    """Accept ISO datetime, datetime, or a 'now[-N<unit>]' relative form.

    Units accepted: seconds, minutes, hours, days, weeks, months, years —
    in short or long spelling (e.g. ``now-15m``, ``now-24h``, ``now-7d``,
    ``now-6mo``, ``now-1y``, ``now-2months``). Anything else is returned
    untouched so Pydantic produces the canonical "this isn't a date" error.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value

    # Relative form first — the common case for small models.
    if _RELATIVE_BARE.match(stripped):
        return datetime.now(UTC)
    rel = _RELATIVE_DELTA.match(stripped)
    if rel:
        amount_str, unit = rel.groups()
        unit_delta = _resolve_unit(unit)
        if unit_delta is not None:
            return datetime.now(UTC) - int(amount_str) * unit_delta
        # Unknown unit: fall through so the value reaches Pydantic, which
        # raises a clear validation error instead of guessing wrong.

    # Otherwise fall through to ISO 8601.  Allow trailing 'Z'.
    iso = stripped.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        return value  # let Pydantic raise the usual error
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def default_time_to() -> datetime:
    """Return 'now' for tool inputs where the model omitted time_to."""
    return datetime.now(UTC)
