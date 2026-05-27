# All the "password" string literals in this file are test-only fixture
# values for the validator's HTTP probe; none of them are real secrets.
# ruff: noqa: S106
"""Tests for Phase 4 Slice 2's connection-validation + re-bootstrap refusal.

The validator probes Wazuh's Indexer (HTTP GET /) and Server API
(POST /security/user/authenticate) BEFORE the tenant is persisted.
Refused profiles never produce a DB row. Re-runs for an already-
validated tenant refuse without --update.

These tests cover the validator's HTTP-shape logic via httpx mocks —
no live Wazuh required. The DB-backed re-bootstrap refusal test uses
the conftest's SQLite fixture path.
"""

import httpx
import pytest
from app.management.bootstrap_tenant import (
    ConnectionValidationError,
    _validate_wazuh_connection,
)


def _client_responder(indexer_status: int, server_api_status: int):
    """Build an httpx MockTransport that returns the given status codes."""
    def _handler(request: httpx.Request) -> httpx.Response:
        if "9200" in str(request.url):
            return httpx.Response(indexer_status, request=request, text="")
        if "55000" in str(request.url):
            return httpx.Response(server_api_status, request=request, text="")
        raise AssertionError(f"unexpected url {request.url}")
    return _handler


@pytest.mark.asyncio
async def test_validator_accepts_two_clean_200s(monkeypatch) -> None:
    """The happy path: both endpoints authenticate cleanly."""
    transport = httpx.MockTransport(_client_responder(200, 200))

    # Patch httpx.AsyncClient construction inside the validator so it
    # uses our MockTransport instead of opening real sockets.
    real_client_cls = httpx.AsyncClient
    monkeypatch.setattr(
        "app.management.bootstrap_tenant.httpx.AsyncClient",
        lambda **kw: real_client_cls(transport=transport, **kw),
    )

    # Should not raise.
    await _validate_wazuh_connection(
        opensearch_url="https://wazuh.example:9200",
        opensearch_username="u",
        opensearch_password="p",
        server_api_url="https://wazuh.example:55000",
        server_api_username="u",
        server_api_password="p",
        verify_tls=False,
    )


@pytest.mark.asyncio
async def test_validator_tolerates_indexer_403_with_auth(monkeypatch) -> None:
    """The 'admin' user authenticates but lacks cluster:monitor — that's
    not a credential failure, just a missing role permission. The cred
    is good enough to satisfy validation."""
    transport = httpx.MockTransport(_client_responder(403, 200))
    real_client_cls = httpx.AsyncClient
    monkeypatch.setattr(
        "app.management.bootstrap_tenant.httpx.AsyncClient",
        lambda **kw: real_client_cls(transport=transport, **kw),
    )

    await _validate_wazuh_connection(
        opensearch_url="https://wazuh.example:9200",
        opensearch_username="u", opensearch_password="p",
        server_api_url="https://wazuh.example:55000",
        server_api_username="u", server_api_password="p",
        verify_tls=False,
    )


@pytest.mark.asyncio
async def test_validator_rejects_indexer_401(monkeypatch) -> None:
    transport = httpx.MockTransport(_client_responder(401, 200))
    real_client_cls = httpx.AsyncClient
    monkeypatch.setattr(
        "app.management.bootstrap_tenant.httpx.AsyncClient",
        lambda **kw: real_client_cls(transport=transport, **kw),
    )

    with pytest.raises(ConnectionValidationError, match="Indexer.*401"):
        await _validate_wazuh_connection(
            opensearch_url="https://wazuh.example:9200",
            opensearch_username="u", opensearch_password="bad",
            server_api_url="https://wazuh.example:55000",
            server_api_username="u", server_api_password="p",
            verify_tls=False,
        )


@pytest.mark.asyncio
async def test_validator_rejects_server_api_401(monkeypatch) -> None:
    transport = httpx.MockTransport(_client_responder(200, 401))
    real_client_cls = httpx.AsyncClient
    monkeypatch.setattr(
        "app.management.bootstrap_tenant.httpx.AsyncClient",
        lambda **kw: real_client_cls(transport=transport, **kw),
    )

    with pytest.raises(ConnectionValidationError, match="Server API.*401"):
        await _validate_wazuh_connection(
            opensearch_url="https://wazuh.example:9200",
            opensearch_username="u", opensearch_password="p",
            server_api_url="https://wazuh.example:55000",
            server_api_username="u", server_api_password="bad",
            verify_tls=False,
        )


@pytest.mark.asyncio
async def test_validator_rejects_unreachable_indexer(monkeypatch) -> None:
    """Network-layer failure (DNS, connection refused, timeout) surfaces
    with the failing endpoint named, not as an opaque exception."""
    def _refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(_refuse)
    real_client_cls = httpx.AsyncClient
    monkeypatch.setattr(
        "app.management.bootstrap_tenant.httpx.AsyncClient",
        lambda **kw: real_client_cls(transport=transport, **kw),
    )

    with pytest.raises(
        ConnectionValidationError, match="Indexer.*unreachable"
    ):
        await _validate_wazuh_connection(
            opensearch_url="https://nowhere.example:9200",
            opensearch_username="u", opensearch_password="p",
            server_api_url="https://nowhere.example:55000",
            server_api_username="u", server_api_password="p",
            verify_tls=False,
        )


@pytest.mark.asyncio
async def test_validator_error_message_mentions_indexer_vs_server_api_split(
    monkeypatch,
) -> None:
    """The 401 message on the Server API leg specifically names the
    Indexer-vs-Server-API user-database split — that's the operational
    gotcha that bit us during Slice 3's end-to-end. The error message
    is part of the contract."""
    transport = httpx.MockTransport(_client_responder(200, 401))
    real_client_cls = httpx.AsyncClient
    monkeypatch.setattr(
        "app.management.bootstrap_tenant.httpx.AsyncClient",
        lambda **kw: real_client_cls(transport=transport, **kw),
    )

    with pytest.raises(ConnectionValidationError) as ctx:
        await _validate_wazuh_connection(
            opensearch_url="https://wazuh.example:9200",
            opensearch_username="u", opensearch_password="p",
            server_api_url="https://wazuh.example:55000",
            server_api_username="u", server_api_password="bad",
            verify_tls=False,
        )
    msg = str(ctx.value)
    assert "Server API has its OWN" in msg or "separate from the Indexer" in msg
