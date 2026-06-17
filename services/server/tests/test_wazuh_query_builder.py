"""Tests for the OrganizationScopedQueryBuilder — the optional group-label filter.

Phase 6.6-f: the builder can force a `terms:{agent.labels.group:[...]}` clause
(the real Wazuh field) onto every query when an org opts into it.  The two
properties pinned here: when ENABLED every method includes the clause with the
configured labels; when DISABLED (the default — the per-org credential is the
isolation boundary) no method emits it.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from wolf_server.wazuh.query_builder import OrganizationScopedQueryBuilder


def _group_label_values(query: dict[str, Any]) -> list[str] | None:
    """Extract the agent.labels.group terms list from the query, or None."""
    filters = query.get("query", {}).get("bool", {}).get("filter", [])
    for clause in filters:
        terms = clause.get("terms", {}) if isinstance(clause, dict) else {}
        if "agent.labels.group" in terms:
            return list(terms["agent.labels.group"])
    return None


@pytest.fixture
def builder() -> OrganizationScopedQueryBuilder:
    # These tests pin the opt-in group-label behaviour, so the filter is
    # explicitly enabled with a single label.  Default (off) is exercised in a
    # separate test below.
    return OrganizationScopedQueryBuilder(
        uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        inject_group_label_filter=True,
        agent_group_labels=["acme"],
    )


@pytest.fixture
def time_window() -> tuple[datetime, datetime]:
    end = datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC)
    return end - timedelta(hours=24), end


# ─── Every builder method enforces the group-label filter when enabled ───────


def test_search_alerts_includes_group_label_filter(
    builder: OrganizationScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    q = builder.search_alerts(time_from=time_window[0], time_to=time_window[1])
    assert _group_label_values(q) == ["acme"]


def test_aggregate_alerts_includes_group_label_filter(
    builder: OrganizationScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    q = builder.aggregate_alerts(
        time_from=time_window[0], time_to=time_window[1], group_by="agent.name"
    )
    assert _group_label_values(q) == ["acme"]


def test_event_timeline_includes_group_label_filter(
    builder: OrganizationScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    q = builder.event_timeline(time_from=time_window[0], time_to=time_window[1], agent_id="001")
    assert _group_label_values(q) == ["acme"]


def test_agent_alert_history_includes_group_label_filter(
    builder: OrganizationScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    q = builder.agent_alert_history(
        time_from=time_window[0], time_to=time_window[1], agent_id="001"
    )
    assert _group_label_values(q) == ["acme"]


# ─── Multiple labels are OR-combined into one terms clause ───────────────────


def test_multiple_labels_are_or_combined(
    time_window: tuple[datetime, datetime],
) -> None:
    multi = OrganizationScopedQueryBuilder(
        uuid.uuid4(),
        inject_group_label_filter=True,
        agent_group_labels=["acme", "acme-eu"],
    )
    q = multi.search_alerts(time_from=time_window[0], time_to=time_window[1])
    assert _group_label_values(q) == ["acme", "acme-eu"]


# ─── There is no escape hatch — no raw_query method exists ───────────────────


def test_no_raw_query_method_exists(builder: OrganizationScopedQueryBuilder) -> None:
    """The builder must not expose a method that bypasses the forced filter."""
    method_names = {n for n in dir(builder) if not n.startswith("_")}
    # Any new public method must build queries via the mandatory_filters
    # path — this test will fail on additions that don't, prompting a
    # deliberate review.  `inject_group_label_filter` + `agent_group_labels`
    # are read-only properties surfacing the per-org configuration; they are
    # not query constructors.
    assert method_names == {
        "search_alerts",
        "aggregate_alerts",
        "event_timeline",
        "agent_alert_history",
        "inject_group_label_filter",
        "agent_group_labels",
    }


# ─── Default mode (off) — the per-org credential is the boundary ─────────────


def test_default_mode_omits_group_label_filter_entirely(
    time_window: tuple[datetime, datetime],
) -> None:
    """Default: no terms:{agent.labels.group} clause anywhere."""
    standalone = OrganizationScopedQueryBuilder(uuid.uuid4())  # inject=False default
    q = standalone.search_alerts(time_from=time_window[0], time_to=time_window[1])
    assert _group_label_values(q) is None
    # The timestamp range is still the only mandatory filter.
    filters = q["query"]["bool"]["filter"]
    assert any("range" in f and "timestamp" in f["range"] for f in filters)


def test_enabled_but_no_labels_omits_filter(
    time_window: tuple[datetime, datetime],
) -> None:
    """Belt-and-suspenders: filter on but no labels → still emits nothing."""
    empty = OrganizationScopedQueryBuilder(
        uuid.uuid4(), inject_group_label_filter=True, agent_group_labels=[]
    )
    q = empty.search_alerts(time_from=time_window[0], time_to=time_window[1])
    assert _group_label_values(q) is None


def test_default_mode_aggregate_omits_group_label_filter(
    time_window: tuple[datetime, datetime],
) -> None:
    standalone = OrganizationScopedQueryBuilder(uuid.uuid4())
    q = standalone.aggregate_alerts(
        time_from=time_window[0], time_to=time_window[1], group_by="rule.level"
    )
    assert _group_label_values(q) is None


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


# ─── Optional filters are additive, not replacing the group-label filter ─────


def test_extra_filters_do_not_replace_group_label_filter(
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
    # Group-label filter still present despite all the additional filters.
    assert _group_label_values(q) == ["acme"]
    # And the extra filters are also present.
    filters = q["query"]["bool"]["filter"]
    flat = {next(iter(f.get("term", {}).keys()), ""): f for f in filters if "term" in f}
    assert "agent.id" in flat
    assert "rule.id" in flat
    assert "rule.mitre.id" in flat
