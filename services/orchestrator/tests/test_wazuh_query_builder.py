"""Tests for the TenantScopedQueryBuilder — proves the tenant filter is mandatory.

The single most important property: **every method on the builder produces a
query containing the tenant filter**, with the correct tenant_id, and the
filter cannot be omitted by any caller.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.wazuh.query_builder import TenantScopedQueryBuilder


def _tenant_filter_value(query: dict[str, Any]) -> str | None:
    """Extract the tenant_id value from the query's filter clauses, or None."""
    filters = query.get("query", {}).get("bool", {}).get("filter", [])
    for clause in filters:
        term = clause.get("term", {}) if isinstance(clause, dict) else {}
        if "tenant_id" in term:
            return str(term["tenant_id"])
    return None


@pytest.fixture
def builder() -> TenantScopedQueryBuilder:
    # These tests pin the multi-tenant pooled-index behaviour, so the
    # filter is explicitly enabled.  Default (False) is exercised in a
    # separate test below.
    return TenantScopedQueryBuilder(
        uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        inject_tenant_filter=True,
    )


@pytest.fixture
def time_window() -> tuple[datetime, datetime]:
    end = datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC)
    return end - timedelta(hours=24), end


# ─── Every builder method enforces the tenant filter ─────────────────────────


def test_search_alerts_includes_tenant_filter(
    builder: TenantScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    q = builder.search_alerts(time_from=time_window[0], time_to=time_window[1])
    assert _tenant_filter_value(q) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_aggregate_alerts_includes_tenant_filter(
    builder: TenantScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    q = builder.aggregate_alerts(
        time_from=time_window[0], time_to=time_window[1], group_by="agent.name"
    )
    assert _tenant_filter_value(q) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_event_timeline_includes_tenant_filter(
    builder: TenantScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    q = builder.event_timeline(
        time_from=time_window[0], time_to=time_window[1], agent_id="001"
    )
    assert _tenant_filter_value(q) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_agent_alert_history_includes_tenant_filter(
    builder: TenantScopedQueryBuilder, time_window: tuple[datetime, datetime]
) -> None:
    q = builder.agent_alert_history(
        time_from=time_window[0], time_to=time_window[1], agent_id="001"
    )
    assert _tenant_filter_value(q) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


# ─── Different tenants produce different filter values ───────────────────────


def test_different_tenants_get_different_filters(
    time_window: tuple[datetime, datetime],
) -> None:
    tenant_a = TenantScopedQueryBuilder(uuid.uuid4(), inject_tenant_filter=True)
    tenant_b = TenantScopedQueryBuilder(uuid.uuid4(), inject_tenant_filter=True)
    qa = tenant_a.search_alerts(time_from=time_window[0], time_to=time_window[1])
    qb = tenant_b.search_alerts(time_from=time_window[0], time_to=time_window[1])
    assert _tenant_filter_value(qa) != _tenant_filter_value(qb)


# ─── There is no escape hatch — no raw_query method exists ───────────────────


def test_no_raw_query_method_exists(builder: TenantScopedQueryBuilder) -> None:
    """The builder must not expose a method that bypasses the tenant filter."""
    method_names = {n for n in dir(builder) if not n.startswith("_")}
    # Any new public method must build queries via the mandatory_filters
    # path — this test will fail on additions that don't, prompting a
    # deliberate review.  `inject_tenant_filter` is the read-only
    # property that surfaces the per-tenant configuration; it is not a
    # query constructor.
    assert method_names == {
        "search_alerts",
        "aggregate_alerts",
        "event_timeline",
        "agent_alert_history",
        "inject_tenant_filter",
    }


# ─── inject_tenant_filter=False (the default for standalone Wazuh) ────────────


def test_default_mode_omits_tenant_filter_entirely(
    time_window: tuple[datetime, datetime],
) -> None:
    """Standalone-deployment default: no term:{tenant_id} clause anywhere."""
    standalone = TenantScopedQueryBuilder(uuid.uuid4())  # inject=False default
    q = standalone.search_alerts(time_from=time_window[0], time_to=time_window[1])
    assert _tenant_filter_value(q) is None
    # The timestamp range is still the only mandatory filter.
    filters = q["query"]["bool"]["filter"]
    assert any("range" in f and "timestamp" in f["range"] for f in filters)


def test_default_mode_aggregate_omits_tenant_filter(
    time_window: tuple[datetime, datetime],
) -> None:
    standalone = TenantScopedQueryBuilder(uuid.uuid4())
    q = standalone.aggregate_alerts(
        time_from=time_window[0], time_to=time_window[1], group_by="rule.level"
    )
    assert _tenant_filter_value(q) is None


# ─── Optional filters are additive, not replacing the tenant filter ──────────


def test_extra_filters_do_not_replace_tenant_filter(
    builder: TenantScopedQueryBuilder, time_window: tuple[datetime, datetime]
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
    # Tenant filter still present despite all the additional filters.
    assert _tenant_filter_value(q) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    # And the extra filters are also present.
    filters = q["query"]["bool"]["filter"]
    flat = {next(iter(f.get("term", {}).keys()), ""): f for f in filters if "term" in f}
    assert "agent.id" in flat
    assert "rule.id" in flat
    assert "rule.mitre.id" in flat
