"""Resource limit definitions and pre-execution checks.

Limits enforced:
  - Maximum time range per *raw-document* query (default 365 days).
    Aggregation/count tools are volume-bounded by bucket count, not by
    window width, so they opt out of this check (``enforce_time_window=
    False``) and can analyze any range.
  - Result-count caps (default 1000 hits per page; cursor pagination via
    ``search_after`` walks larger result sets gap-free — see query_builder).
  - Context-volume limits (default 100 KB of returned data into model context).

The time-window cap is deliberately generous: result volume is already
bounded per page by ``max_results_per_query`` and by context truncation, so
a wide window with ``size=100`` simply returns the 100 most-recent matches.
The window cap is a backstop against a pathological single-page request, not
the primary volume guard.

Limits are dataclass-configurable so an operator can tighten or loosen them
per deployment, but the defaults are safe for typical organizations.

Per-organization overrides could live in a future `organization_resource_limits` table;
the function signature already accepts a `ResourceLimits` so the dispatcher
can swap in a organization-specific instance without changing the call site.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from wolf_common.errors import WolfError


class GuardrailViolation(WolfError):  # noqa: N818 — Wolf convention: violations are errors-by-base-class
    """A tool call exceeded a resource guardrail.  Rejected before execution."""

    http_status = 422
    error_code = "guardrail_violation"


@dataclass(frozen=True)
class ResourceLimits:
    """Operator-tunable resource limits.  Sensible defaults; tighten in prod."""

    max_time_range: timedelta = field(default_factory=lambda: timedelta(days=365))
    max_results_per_query: int = 1000
    max_context_bytes: int = 100_000


DEFAULT_LIMITS = ResourceLimits()

# Grace added to the time-range ceiling. A model asking for "now-30d" and
# letting time_to default to "now" parses the two `now`s microseconds apart
# (field validators run sequentially), so the span lands at 30d + a few µs and
# a strict `>` rejects an obviously-intended-30-day query. One second of slack
# absorbs that drift without meaningfully widening the limit.
_TIME_RANGE_GRACE = timedelta(seconds=1)


def enforce_limits(
    *,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    requested_size: int | None = None,
    limits: ResourceLimits = DEFAULT_LIMITS,
    enforce_time_window: bool = True,
) -> None:
    """Raise GuardrailViolation if any limit is exceeded.

    Call with only the fields relevant to the tool — unrelated fields
    default to None and are skipped.

    ``enforce_time_window=False`` keeps the inverted-window correctness
    check but skips the max-width cap. Aggregation/count tools use this:
    they return bucketed counts whose volume is bounded by bucket count,
    so a multi-month or full-year rollup is cheap and complete.
    """
    if time_from is not None and time_to is not None:
        if time_to < time_from:
            raise GuardrailViolation("time_to is before time_from")
        span = time_to - time_from
        if enforce_time_window and span > limits.max_time_range + _TIME_RANGE_GRACE:
            raise GuardrailViolation(f"Query spans {span} > maximum {limits.max_time_range}")

    if requested_size is not None and requested_size > limits.max_results_per_query:
        raise GuardrailViolation(
            f"Requested {requested_size} results > max {limits.max_results_per_query}; paginate"
        )


def truncate_for_context(payload: str, limits: ResourceLimits = DEFAULT_LIMITS) -> str:
    """Truncate a string payload to fit the model's context-volume budget.

    Appends a clear marker so the model knows the truncation occurred and
    can ask for a narrower query.
    """
    if len(payload.encode("utf-8")) <= limits.max_context_bytes:
        return payload
    # Encode-aware truncation to avoid splitting a multi-byte char.
    encoded = payload.encode("utf-8")[: limits.max_context_bytes]
    safe = encoded.decode("utf-8", errors="ignore")
    return safe + "\n\n[... truncated by resource guardrail; narrow your query ...]"
