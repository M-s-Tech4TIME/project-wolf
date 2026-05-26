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
from app.tools.alerts import SearchAlertsInput, SearchAlertsTool
from app.tools.base import ToolExecContext


def _ctx(opensearch: Any, server_api: Any) -> ToolExecContext:
    return ToolExecContext(
        tenant=TenantContext(
            tenant_id=uuid.uuid4(),
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
