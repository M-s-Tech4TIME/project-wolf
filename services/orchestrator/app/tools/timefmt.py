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

# now, now-15m, now-2h, now-1d, now-7d, now-30s
_RELATIVE_PATTERN = re.compile(r"^now(?:-(\d+)(s|m|h|d|w))?$", re.IGNORECASE)

_UNIT_TO_DELTA: dict[str, timedelta] = {
    "s": timedelta(seconds=1),
    "m": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
}


def parse_time_field(value: Any) -> datetime | Any:
    """Accept ISO datetime, datetime, or 'now[-N{s,m,h,d,w}]' relative form.

    Anything else is returned untouched so Pydantic can produce the
    canonical "this isn't a date" error.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value

    # Relative form first — the common case for small models.
    rel = _RELATIVE_PATTERN.match(stripped)
    if rel:
        now = datetime.now(UTC)
        amount_str, unit = rel.groups()
        if amount_str is None:
            return now
        unit_delta = _UNIT_TO_DELTA[unit.lower()]
        return now - int(amount_str) * unit_delta

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
