"""Slice 3 follow-up — agent_name lookup in search_alerts.

Verifies the small-model-confusion mitigation: when a model passes
`agent_name="linux-test-agent"` instead of `agent_id="001"`, the tool
resolves the name via the Server API's `/agents?name=` filter before
running the alerts query.
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.guardrails.limits import DEFAULT_LIMITS
from app.tenancy.context import TenantContext
from app.tools.alerts import SearchAlertsInput, SearchAlertsOutput, SearchAlertsTool
from app.tools.base import ToolExecContext


def _ctx(
    opensearch: Any,
    server_api: Any,
    cache: Any = None,
    tenant_id: uuid.UUID | None = None,
) -> ToolExecContext:
    return ToolExecContext(
        tenant=TenantContext(
            tenant_id=tenant_id or uuid.uuid4(),
            tenant_slug="acme",
            user_id=uuid.uuid4(),
            user_email="t@example.com",
            role="analyst",
            session_id="test",
        ),
        limits=DEFAULT_LIMITS,
        opensearch=opensearch,
        server_api=server_api,
        knowledge_store=None,
        cache=cache,
    )


def _opensearch_returning(hits: list[Any] | None = None) -> Any:
    """OpenSearch stub that records the query it was called with."""
    qb = MagicMock()
    qb.search_alerts = MagicMock(return_value={"query": "stub"})
    os_client = MagicMock()
    os_client.query_builder = qb
    os_client.execute = AsyncMock(
        return_value={"hits": {"hits": hits or [], "total": {"value": len(hits or [])}}}
    )
    return os_client


def _server_api_finding(agent_id: str | None) -> Any:
    server = MagicMock()
    items: list[dict[str, Any]] = (
        [{"id": agent_id}] if agent_id is not None else []
    )
    server.get = AsyncMock(return_value={"data": {"affected_items": items}})
    return server


# ─── Happy paths ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_name_resolves_to_id_before_query() -> None:
    """The Slice 3 fix: name in, id out, search runs against the id."""
    os_client = _opensearch_returning()
    server = _server_api_finding(agent_id="001")
    tool = SearchAlertsTool()
    args = SearchAlertsInput(time_from="now-1h", agent_name="linux-test-agent")
    await tool.run(_ctx(os_client, server), args)

    # Server API was queried for the name.
    server.get.assert_awaited_once_with("/agents", params={"name": "linux-test-agent"})
    # OpenSearch query builder was called with the RESOLVED id, not the name.
    kwargs = os_client.query_builder.search_alerts.call_args.kwargs
    assert kwargs["agent_id"] == "001"


@pytest.mark.asyncio
async def test_agent_id_takes_precedence_over_agent_name() -> None:
    """If both are supplied the explicit ID wins — no API call needed."""
    os_client = _opensearch_returning()
    server = _server_api_finding(agent_id="999")
    tool = SearchAlertsTool()
    args = SearchAlertsInput(
        time_from="now-1h", agent_id="001", agent_name="linux-test-agent"
    )
    await tool.run(_ctx(os_client, server), args)

    server.get.assert_not_called()
    kwargs = os_client.query_builder.search_alerts.call_args.kwargs
    assert kwargs["agent_id"] == "001"


# ─── Edge cases ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_name_unresolvable_runs_unfiltered_query() -> None:
    """Server API returns no matching agent → query runs without agent_id.

    Chosen over raising: the validator catches the resulting under-
    grounding, and an exception would shape model-recoverable failures
    awkwardly. The model can re-query with the correct name on next
    turn if the loop continues."""
    os_client = _opensearch_returning()
    server = _server_api_finding(agent_id=None)  # empty affected_items
    tool = SearchAlertsTool()
    args = SearchAlertsInput(time_from="now-1h", agent_name="nonexistent-agent")
    await tool.run(_ctx(os_client, server), args)

    server.get.assert_awaited_once()
    kwargs = os_client.query_builder.search_alerts.call_args.kwargs
    assert kwargs["agent_id"] is None


@pytest.mark.asyncio
async def test_neither_id_nor_name_means_no_agent_filter() -> None:
    """The all-agents query still works — no Server API call wasted."""
    os_client = _opensearch_returning()
    server = _server_api_finding(agent_id=None)
    tool = SearchAlertsTool()
    args = SearchAlertsInput(time_from="now-1h")
    await tool.run(_ctx(os_client, server), args)

    server.get.assert_not_called()
    kwargs = os_client.query_builder.search_alerts.call_args.kwargs
    assert kwargs["agent_id"] is None


# ─── Cursor pagination output (Slice 5.0a) ──────────────────────────────────


def _opensearch_page(hits: list[Any], total: int) -> Any:
    os_client = MagicMock()
    os_client.query_builder = MagicMock()
    os_client.query_builder.search_alerts = MagicMock(return_value={"query": "stub"})
    os_client.execute = AsyncMock(
        return_value={"hits": {"hits": hits, "total": {"value": total}}}
    )
    return os_client


def _hit(doc_id: str, sort: list[Any]) -> dict[str, Any]:
    return {"_id": doc_id, "_source": {"timestamp": "2026-05-27T00:00:00Z"}, "sort": sort}


@pytest.mark.asyncio
async def test_full_page_reports_has_more_and_next_cursor() -> None:
    """A page filled to `size` signals more rows and exposes the cursor."""
    sort_vals = [1779887919336, "abc"]
    hits = [_hit(str(i), [1779887919336 - i, f"id{i}"]) for i in range(3)]
    hits[-1]["sort"] = sort_vals
    os_client = _opensearch_page(hits, total=50)
    args = SearchAlertsInput(time_from="now-1h", size=3)
    out = await SearchAlertsTool().run(_ctx(os_client, _server_api_finding(None)), args)
    assert isinstance(out, SearchAlertsOutput)
    assert out.has_more is True
    assert out.next_cursor == sort_vals
    assert out.total == 50


@pytest.mark.asyncio
async def test_short_page_reports_done_and_null_cursor() -> None:
    """A page shorter than `size` means the walk is complete."""
    hits = [_hit("0", [1, "a"]), _hit("1", [2, "b"])]
    os_client = _opensearch_page(hits, total=2)
    args = SearchAlertsInput(time_from="now-1h", size=100)
    out = await SearchAlertsTool().run(_ctx(os_client, _server_api_finding(None)), args)
    assert isinstance(out, SearchAlertsOutput)
    assert out.has_more is False
    assert out.next_cursor is None


@pytest.mark.asyncio
async def test_cursor_is_forwarded_to_query_builder() -> None:
    """The input cursor reaches the builder as search_after."""
    cursor = [1779887919336, "abc"]
    os_client = _opensearch_page([], total=0)
    args = SearchAlertsInput(time_from="now-1h", cursor=cursor)
    await SearchAlertsTool().run(_ctx(os_client, _server_api_finding(None)), args)
    kwargs = os_client.query_builder.search_alerts.call_args.kwargs
    assert kwargs["search_after"] == cursor


# ─── Cache behavior (Phase 4 Slice 3) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_name_cache_hit_skips_server_api_call() -> None:
    """Second resolution of the same agent_name re-uses the cached id
    instead of re-hitting the Server API. The cache is per-tenant by
    construction (doc 05 §Caching across tenants)."""
    from app.caching import InMemoryTenantCache

    cache = InMemoryTenantCache()
    tenant_id = uuid.uuid4()

    os_client = _opensearch_returning()
    server = _server_api_finding(agent_id="001")
    ctx = _ctx(os_client, server, cache=cache, tenant_id=tenant_id)
    tool = SearchAlertsTool()

    # First call populates the cache.
    args1 = SearchAlertsInput(time_from="now-1h", agent_name="linux-test-agent")
    await tool.run(ctx, args1)
    assert server.get.await_count == 1

    # Second call with same tenant + same name → cache hit, no extra API call.
    args2 = SearchAlertsInput(time_from="now-30m", agent_name="linux-test-agent")
    await tool.run(ctx, args2)
    assert server.get.await_count == 1  # unchanged from the first call


@pytest.mark.asyncio
async def test_agent_name_cache_is_tenant_scoped() -> None:
    """Tenant A's cached resolution must NOT satisfy tenant B's lookup.
    Each tenant's cache entry is keyed by its own tenant_id; the same
    `agent_name` string can map to different agent_ids in different
    Wazuh deployments."""
    from app.caching import InMemoryTenantCache

    cache = InMemoryTenantCache()
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    # Two separate server-api stubs because each tenant probes its
    # own Wazuh; we use the same agent_name but different resolved IDs.
    server_a = _server_api_finding(agent_id="001")
    server_b = _server_api_finding(agent_id="999")
    tool = SearchAlertsTool()

    # Tenant A resolves the name → caches "001"
    await tool.run(
        _ctx(_opensearch_returning(), server_a, cache=cache, tenant_id=tenant_a),
        SearchAlertsInput(time_from="now-1h", agent_name="my-host"),
    )
    assert server_a.get.await_count == 1

    # Tenant B resolves the SAME name → must NOT hit A's cache entry;
    # B's own server-api stub is consulted.
    await tool.run(
        _ctx(_opensearch_returning(), server_b, cache=cache, tenant_id=tenant_b),
        SearchAlertsInput(time_from="now-1h", agent_name="my-host"),
    )
    assert server_b.get.await_count == 1, (
        "Cross-tenant cache leak: tenant B's agent_name resolution "
        "satisfied by tenant A's cached entry."
    )


@pytest.mark.asyncio
async def test_agent_name_not_found_is_cached_as_sentinel() -> None:
    """Negative results are cached too — re-asking for a non-existent
    name should not re-probe the API."""
    from app.caching import InMemoryTenantCache

    cache = InMemoryTenantCache()
    tenant_id = uuid.uuid4()

    server = _server_api_finding(agent_id=None)
    ctx = _ctx(_opensearch_returning(), server, cache=cache, tenant_id=tenant_id)
    tool = SearchAlertsTool()

    args = SearchAlertsInput(time_from="now-1h", agent_name="nonexistent")
    await tool.run(ctx, args)
    await tool.run(ctx, args)
    # Two calls, one API probe — negative result is cached.
    assert server.get.await_count == 1
