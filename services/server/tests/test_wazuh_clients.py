"""Tests for WazuhOpenSearchClient and WazuhServerApiClient.

Uses an httpx MockTransport so the tests don't hit a real Wazuh.  Focus is
on the safety-critical paths:
  - OpenSearch rejects queries missing the group-label filter when an org has
    opted into it (defense in depth)
  - OpenSearch raises OrganizationMismatchError when a returned doc's
    agent.labels.group is outside the allowed labels
  - Server API client is read-only (no POST/PUT/DELETE)
  - Server API client transparently authenticates and refreshes on 401
"""

import uuid
from typing import Any

import httpx
import pytest
from wolf_common.errors import OrganizationMismatchError
from wolf_server.wazuh.config import WazuhConnection
from wolf_server.wazuh.opensearch import WazuhOpenSearchClient, WazuhOpenSearchError
from wolf_server.wazuh.server_api import WazuhServerApiClient, WazuhServerApiError

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def organization_id() -> uuid.UUID:
    return uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


@pytest.fixture
def connection(organization_id: uuid.UUID) -> WazuhConnection:
    return WazuhConnection(
        organization_id=organization_id,
        opensearch_url="https://os.example.test:9200",
        opensearch_index_pattern="wazuh-alerts-*",
        opensearch_username="wolf_ro",
        opensearch_password="secret",  # noqa: S106 — test fixture
        server_api_url="https://api.example.test:55000",
        server_api_username="wolf_api",
        server_api_password="secret",  # noqa: S106 — test fixture
        verify_tls=True,
        inject_group_label_filter=True,
        agent_group_labels=("acme",),
    )


def _make_os_client(
    connection: WazuhConnection,
    handler: httpx.AsyncBaseTransport,
) -> WazuhOpenSearchClient:
    http = httpx.AsyncClient(
        base_url=connection.opensearch_url,
        auth=(connection.opensearch_username, connection.opensearch_password),
        transport=handler,
        timeout=5.0,
    )
    return WazuhOpenSearchClient(connection, client=http)


# ─── OpenSearch client ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_opensearch_rejects_query_missing_group_label_filter(
    connection: WazuhConnection,
) -> None:
    """A hand-crafted query without the group-label filter must be rejected."""

    async def _never_called(_req: httpx.Request) -> httpx.Response:
        raise AssertionError("Request must not reach transport")

    transport = httpx.MockTransport(_never_called)
    client = _make_os_client(connection, transport)
    bad_query: dict[str, Any] = {"query": {"bool": {"filter": []}}}
    with pytest.raises(OrganizationMismatchError, match="agent.labels.group filter"):
        await client.execute(bad_query)


@pytest.mark.asyncio
async def test_opensearch_rejects_returned_doc_with_wrong_group_label(
    connection: WazuhConnection, organization_id: uuid.UUID
) -> None:
    """Defense in depth: a doc whose agent.labels.group is out of scope hard-fails."""

    async def _handler(request: httpx.Request) -> httpx.Response:
        body = {
            "hits": {
                "total": {"value": 1},
                "hits": [
                    {
                        "_id": "doc1",
                        "_source": {
                            "agent": {"labels": {"group": "evil"}},
                            "rule": {"id": "5710"},
                        },
                    }
                ],
            }
        }
        return httpx.Response(200, json=body, request=request)

    transport = httpx.MockTransport(_handler)
    client = _make_os_client(connection, transport)
    # Build the query via the org builder so the outbound check passes.
    from datetime import UTC, datetime, timedelta

    q = client.query_builder.search_alerts(
        time_from=datetime.now(UTC) - timedelta(hours=1),
        time_to=datetime.now(UTC),
    )
    with pytest.raises(OrganizationMismatchError, match="agent.labels.group"):
        await client.execute(q)


@pytest.mark.asyncio
async def test_opensearch_returns_body_on_success(
    connection: WazuhConnection, organization_id: uuid.UUID
) -> None:
    expected_body = {"hits": {"total": {"value": 0}, "hits": []}}

    async def _handler(request: httpx.Request) -> httpx.Response:
        # Index pattern should be in the path.
        assert "wazuh-alerts-*" in request.url.path
        return httpx.Response(200, json=expected_body, request=request)

    transport = httpx.MockTransport(_handler)
    client = _make_os_client(connection, transport)
    from datetime import UTC, datetime, timedelta

    q = client.query_builder.search_alerts(
        time_from=datetime.now(UTC) - timedelta(hours=1),
        time_to=datetime.now(UTC),
    )
    body = await client.execute(q)
    assert body == expected_body


@pytest.mark.asyncio
async def test_opensearch_raises_on_http_error(
    connection: WazuhConnection,
) -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="indexer unavailable", request=request)

    transport = httpx.MockTransport(_handler)
    client = _make_os_client(connection, transport)
    from datetime import UTC, datetime, timedelta

    q = client.query_builder.search_alerts(
        time_from=datetime.now(UTC) - timedelta(hours=1),
        time_to=datetime.now(UTC),
    )
    with pytest.raises(WazuhOpenSearchError):
        await client.execute(q)


@pytest.mark.asyncio
async def test_opensearch_fails_over_to_fallback_node(organization_id: uuid.UUID) -> None:
    """Phase 6.6-g: a transport error on the primary indexer retries the fallback."""
    primary = "https://idx-primary.test:9200"
    fallback = "https://idx-fallback.test:9200"
    conn = WazuhConnection(
        organization_id=organization_id,
        opensearch_url=primary,
        opensearch_index_pattern="wazuh-alerts-*",
        opensearch_username="u", opensearch_password="p",  # noqa: S106
        server_api_url="https://api.test:55000",
        server_api_username="u", server_api_password="p",  # noqa: S106
        verify_tls=True,
        opensearch_fallback_urls=(fallback,),
    )
    seen: list[str] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.host == "idx-primary.test":
            raise httpx.ConnectError("primary down", request=request)
        return httpx.Response(200, json={"hits": {"total": {"value": 0}, "hits": []}})

    client = _make_os_client(conn, httpx.MockTransport(_handler))
    from datetime import UTC, datetime, timedelta

    q = client.query_builder.search_alerts(
        time_from=datetime.now(UTC) - timedelta(hours=1), time_to=datetime.now(UTC),
    )
    body = await client.execute(q)
    assert body == {"hits": {"total": {"value": 0}, "hits": []}}
    assert seen == ["idx-primary.test", "idx-fallback.test"]  # tried primary, then fell over


@pytest.mark.asyncio
async def test_opensearch_all_nodes_down_raises(organization_id: uuid.UUID) -> None:
    conn = WazuhConnection(
        organization_id=organization_id,
        opensearch_url="https://a.test:9200",
        opensearch_index_pattern="wazuh-alerts-*",
        opensearch_username="u", opensearch_password="p",  # noqa: S106
        server_api_url="https://api.test:55000",
        server_api_username="u", server_api_password="p",  # noqa: S106
        verify_tls=True,
        opensearch_fallback_urls=("https://b.test:9200",),
    )

    async def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    client = _make_os_client(conn, httpx.MockTransport(_handler))
    from datetime import UTC, datetime, timedelta

    q = client.query_builder.search_alerts(
        time_from=datetime.now(UTC) - timedelta(hours=1), time_to=datetime.now(UTC),
    )
    with pytest.raises(WazuhOpenSearchError, match="indexer node"):
        await client.execute(q)


# ─── Server API client ───────────────────────────────────────────────────────


def _make_api_client(
    connection: WazuhConnection,
    handler: httpx.AsyncBaseTransport,
) -> WazuhServerApiClient:
    http = httpx.AsyncClient(
        base_url=connection.server_api_url,
        transport=handler,
        timeout=5.0,
    )
    return WazuhServerApiClient(connection, client=http)


@pytest.mark.asyncio
async def test_server_api_authenticates_and_includes_bearer(
    connection: WazuhConnection,
) -> None:
    captured: dict[str, Any] = {}

    async def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/security/user/authenticate":
            return httpx.Response(200, json={"data": {"token": "test-jwt-token"}}, request=request)
        captured["auth_header"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": {"affected_items": []}}, request=request)

    transport = httpx.MockTransport(_handler)
    client = _make_api_client(connection, transport)
    body = await client.get("/agents")
    assert body == {"data": {"affected_items": []}}
    assert captured["auth_header"] == "Bearer test-jwt-token"


@pytest.mark.asyncio
async def test_server_api_refreshes_token_on_401(
    connection: WazuhConnection,
) -> None:
    call_count = {"auth": 0, "get": 0}

    async def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/security/user/authenticate":
            call_count["auth"] += 1
            return httpx.Response(
                200, json={"data": {"token": f"token-{call_count['auth']}"}}, request=request
            )
        call_count["get"] += 1
        if call_count["get"] == 1:
            return httpx.Response(401, text="expired", request=request)
        return httpx.Response(200, json={"data": {"affected_items": []}}, request=request)

    transport = httpx.MockTransport(_handler)
    client = _make_api_client(connection, transport)
    body = await client.get("/agents")
    assert body == {"data": {"affected_items": []}}
    assert call_count["auth"] == 2  # initial + refresh after 401


@pytest.mark.asyncio
async def test_server_api_rejects_non_get(connection: WazuhConnection) -> None:
    """Belt-and-braces: the read-only client must never permit POST/PUT/DELETE."""

    async def _never(_req: httpx.Request) -> httpx.Response:
        raise AssertionError("Should not reach transport")

    transport = httpx.MockTransport(_never)
    client = _make_api_client(connection, transport)
    with pytest.raises(WazuhServerApiError, match="read-only"):
        # Reach into _request to simulate an internal misuse.
        await client._request("POST", "/agents/restart")


@pytest.mark.asyncio
async def test_server_api_get_raw_returns_text_body(connection: WazuhConnection) -> None:
    """get_raw returns the response body verbatim (XML), not a JSON envelope —
    the rule_tuning snapshot needs the exact file bytes (6-e.3)."""
    raw_xml = '<group name="sshd,">\n  <rule id="100001" level="5"></rule>\n</group>'

    async def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/security/user/authenticate":
            return httpx.Response(200, json={"data": {"token": "jwt"}}, request=request)
        assert request.method == "GET"
        assert request.url.path == "/rules/files/local_rules.xml"
        return httpx.Response(200, text=raw_xml, request=request)

    client = _make_api_client(connection, httpx.MockTransport(_handler))
    body = await client.get_raw(
        "/rules/files/local_rules.xml", params={"raw": "true", "relative_dirname": "etc/rules"}
    )
    assert body == raw_xml  # exact bytes, not parsed
