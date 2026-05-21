"""Tests for WazuhOpenSearchClient and WazuhServerApiClient.

Uses an httpx MockTransport so the tests don't hit a real Wazuh.  Focus is
on the safety-critical paths:
  - OpenSearch rejects queries missing the tenant filter (defense in depth)
  - OpenSearch raises TenantMismatchError when a returned doc has wrong tenant_id
  - Server API client is read-only (no POST/PUT/DELETE)
  - Server API client transparently authenticates and refreshes on 401
"""

import uuid
from typing import Any

import httpx
import pytest
from app.wazuh.config import WazuhConnection
from app.wazuh.opensearch import WazuhOpenSearchClient, WazuhOpenSearchError
from app.wazuh.server_api import WazuhServerApiClient, WazuhServerApiError
from wolf_common.errors import TenantMismatchError

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tenant_id() -> uuid.UUID:
    return uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


@pytest.fixture
def connection(tenant_id: uuid.UUID) -> WazuhConnection:
    return WazuhConnection(
        tenant_id=tenant_id,
        opensearch_url="https://os.example.test:9200",
        opensearch_index_pattern="wazuh-alerts-*",
        opensearch_username="wolf_ro",
        opensearch_password="secret",  # noqa: S106 — test fixture
        server_api_url="https://api.example.test:55000",
        server_api_username="wolf_api",
        server_api_password="secret",  # noqa: S106 — test fixture
        verify_tls=True,
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
async def test_opensearch_rejects_query_missing_tenant_filter(
    connection: WazuhConnection,
) -> None:
    """A hand-crafted query without the tenant filter must be rejected."""

    async def _never_called(_req: httpx.Request) -> httpx.Response:
        raise AssertionError("Request must not reach transport")

    transport = httpx.MockTransport(_never_called)
    client = _make_os_client(connection, transport)
    bad_query: dict[str, Any] = {"query": {"bool": {"filter": []}}}
    with pytest.raises(TenantMismatchError, match="tenant_id filter"):
        await client.execute(bad_query)


@pytest.mark.asyncio
async def test_opensearch_rejects_returned_doc_with_wrong_tenant(
    connection: WazuhConnection, tenant_id: uuid.UUID
) -> None:
    """Defense in depth: doc whose _source.tenant_id mismatches must hard-fail."""
    other = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    async def _handler(request: httpx.Request) -> httpx.Response:
        body = {
            "hits": {
                "total": {"value": 1},
                "hits": [
                    {"_id": "doc1", "_source": {"tenant_id": other, "rule": {"id": "5710"}}}
                ],
            }
        }
        return httpx.Response(200, json=body, request=request)

    transport = httpx.MockTransport(_handler)
    client = _make_os_client(connection, transport)
    # Build the query via the tenant builder so the outbound check passes.
    from datetime import UTC, datetime, timedelta

    q = client.query_builder.search_alerts(
        time_from=datetime.now(UTC) - timedelta(hours=1),
        time_to=datetime.now(UTC),
    )
    with pytest.raises(TenantMismatchError, match="OpenSearch returned doc"):
        await client.execute(q)


@pytest.mark.asyncio
async def test_opensearch_returns_body_on_success(
    connection: WazuhConnection, tenant_id: uuid.UUID
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
            return httpx.Response(
                200, json={"data": {"token": "test-jwt-token"}}, request=request
            )
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
        return httpx.Response(
            200, json={"data": {"affected_items": []}}, request=request
        )

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
