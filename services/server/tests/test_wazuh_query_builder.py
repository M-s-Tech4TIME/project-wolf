"""Tests for the OrganizationScopedQueryBuilder — proves the organization filter is mandatory.

The single most important property: **every method on the builder produces a
query containing the organization filter**, with the correct organization_id, and the
filter cannot be omitted by any caller.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from wolf_server.wazuh.query_builder import OrganizationScopedQueryBuilder


def _organization_filter_value(query: dict[str, Any]) -> str | None:
    """Extract the organization_id value from the query's filter clauses, or None."""
    filters = query.get("query", {}).get("bool", {}).get("filter", [])
    for clause in filters:
        term = clause.get("term", {}) if isinstance(clause, dict) else {}
        if "organization_id" in term:
            return str(term["organization_id"])
    return None


@pytest.fixture
def builder() -> OrganizationScopedQueryBuilder:
    # These tests pin the multi-organization pooled-index behaviour, so the
    # filter is explicitly enabled.  Default (False) is exercised in a
    # separate test below.
    return OrganizationScopedQueryBuilder(
        uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        inject_organization_filter=True,
    )


@pytest.fixture
def time_window() -> tuple[datetime, datetime]:
    end = datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC)
    return end - timedelta(hours=24), end


# ─── Every builder method enforces the organization filter ─────────────────────────


def test_search_alerts_includes_organization_filter(
    builder: OrganizationScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    q = builder.search_alerts(time_from=time_window[0], time_to=time_window[1])
    assert _organization_filter_value(q) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_aggregate_alerts_includes_organization_filter(
    builder: OrganizationScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    q = builder.aggregate_alerts(
        time_from=time_window[0], time_to=time_window[1], group_by="agent.name"
    )
    assert _organization_filter_value(q) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_event_timeline_includes_organization_filter(
    builder: OrganizationScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    q = builder.event_timeline(time_from=time_window[0], time_to=time_window[1], agent_id="001")
    assert _organization_filter_value(q) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_agent_alert_history_includes_organization_filter(
    builder: OrganizationScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    q = builder.agent_alert_history(
        time_from=time_window[0], time_to=time_window[1], agent_id="001"
    )
    assert _organization_filter_value(q) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


# ─── Different organizations produce different filter values ───────────────────────


def test_different_organizations_get_different_filters(
    time_window: tuple[datetime, datetime],
) -> None:
    organization_a = OrganizationScopedQueryBuilder(uuid.uuid4(), inject_organization_filter=True)
    organization_b = OrganizationScopedQueryBuilder(uuid.uuid4(), inject_organization_filter=True)
    qa = organization_a.search_alerts(time_from=time_window[0], time_to=time_window[1])
    qb = organization_b.search_alerts(time_from=time_window[0], time_to=time_window[1])
    assert _organization_filter_value(qa) != _organization_filter_value(qb)


# ─── There is no escape hatch — no raw_query method exists ───────────────────


def test_no_raw_query_method_exists(builder: OrganizationScopedQueryBuilder) -> None:
    """The builder must not expose a method that bypasses the organization filter."""
    method_names = {n for n in dir(builder) if not n.startswith("_")}
    # Any new public method must build queries via the mandatory_filters
    # path — this test will fail on additions that don't, prompting a
    # deliberate review.  `inject_organization_filter` is the read-only
    # property that surfaces the per-organization configuration; it is not a
    # query constructor.
    assert method_names == {
        "search_alerts",
        "aggregate_alerts",
        "event_timeline",
        "agent_alert_history",
        "inject_organization_filter",
    }


# ─── inject_organization_filter=False (the default for standalone Wazuh) ────────────


def test_default_mode_omits_organization_filter_entirely(
    time_window: tuple[datetime, datetime],
) -> None:
    """Standalone-deployment default: no term:{organization_id} clause anywhere."""
    standalone = OrganizationScopedQueryBuilder(uuid.uuid4())  # inject=False default
    q = standalone.search_alerts(time_from=time_window[0], time_to=time_window[1])
    assert _organization_filter_value(q) is None
    # The timestamp range is still the only mandatory filter.
    filters = q["query"]["bool"]["filter"]
    assert any("range" in f and "timestamp" in f["range"] for f in filters)


def test_default_mode_aggregate_omits_organization_filter(
    time_window: tuple[datetime, datetime],
) -> None:
    standalone = OrganizationScopedQueryBuilder(uuid.uuid4())
    q = standalone.aggregate_alerts(
        time_from=time_window[0], time_to=time_window[1], group_by="rule.level"
    )
    assert _organization_filter_value(q) is None


# ─── free_text matches Wazuh's keyword/text field mapping (Slice 5.0a) ───────


def _free_text_should(query: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the should-clauses of the free_text sub-query, or []."""
    for clause in query.get("query", {}).get("bool", {}).get("must", []):
        should = clause.get("bool", {}).get("should")
        if should is not None:
            return should  # type: ignore[no-any-return]
    return []


def test_free_text_searches_full_log_and_rule_description(
    builder: OrganizationScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    """rule.description is keyword-mapped: it needs a wildcard, not match.

    Regression for the Slice 5.0a bug where a multi_match returned 0 hits
    for "SSH brute-force" because the most useful field could not be
    partial-matched.
    """
    q = builder.search_alerts(
        time_from=time_window[0],
        time_to=time_window[1],
        free_text="SSH brute-force",
    )
    should = _free_text_should(q)
    # An analyzed match on the text-mapped full_log.
    assert any("match" in c and "full_log" in c["match"] for c in should)
    # Case-insensitive wildcards on the keyword-mapped rule.description,
    # one per token, with the hyphen normalized to a word break.
    wildcards = [c["wildcard"]["rule.description"] for c in should if "wildcard" in c]
    patterns = {w["value"] for w in wildcards}
    assert patterns == {"*ssh*", "*brute*", "*force*"}
    assert all(w["case_insensitive"] for w in wildcards)


def test_free_text_requires_at_least_one_should_match(
    builder: OrganizationScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    q = builder.search_alerts(time_from=time_window[0], time_to=time_window[1], free_text="sshd")
    must = q["query"]["bool"]["must"]
    assert must[0]["bool"]["minimum_should_match"] == 1


def test_no_free_text_means_empty_must(
    builder: OrganizationScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    q = builder.search_alerts(time_from=time_window[0], time_to=time_window[1])
    assert q["query"]["bool"]["must"] == []


# ─── Cursor pagination via search_after (Slice 5.0a) ─────────────────────────


def test_search_alerts_sort_has_unique_tiebreaker(
    builder: OrganizationScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    """search_after needs a total order: timestamp alone is not unique."""
    q = builder.search_alerts(time_from=time_window[0], time_to=time_window[1])
    sort_fields = [next(iter(s.keys())) for s in q["sort"]]
    assert sort_fields == ["timestamp", "_id"]


def test_search_alerts_omits_search_after_by_default(
    builder: OrganizationScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    q = builder.search_alerts(time_from=time_window[0], time_to=time_window[1])
    assert "search_after" not in q


def test_search_alerts_passes_cursor_through_as_search_after(
    builder: OrganizationScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    cursor = [1779887919336, "dQeWaZ4B8cw0pYlWbI9w"]
    q = builder.search_alerts(time_from=time_window[0], time_to=time_window[1], search_after=cursor)
    assert q["search_after"] == cursor


# ─── Optional filters are additive, not replacing the organization filter ──────────


def test_extra_filters_do_not_replace_organization_filter(
    builder: OrganizationScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    q = builder.search_alerts(
        time_from=time_window[0],
        time_to=time_window[1],
        agent_id="001",
        rule_id=5710,
        min_level=10,
        attack_technique="T1110",
        free_text="failed login",
    )
    # Organization filter still present despite all the additional filters.
    assert _organization_filter_value(q) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    # And the extra filters are also present.
    filters = q["query"]["bool"]["filter"]
    flat = {next(iter(f.get("term", {}).keys()), ""): f for f in filters if "term" in f}
    assert "agent.id" in flat
    assert "rule.id" in flat
    assert "rule.mitre.id" in flat
