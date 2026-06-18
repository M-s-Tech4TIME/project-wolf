"""Bounded write surface — WazuhServerApiActionClient (Phase 6, ADR 0025).

The write client must (1) refuse an action the credential's RBAC forbids
BEFORE issuing any request (fail-closed), and (2) issue exactly the whitelisted
PUT /active-response when permitted.  Uses an httpx MockTransport — no real Wazuh.
"""

import uuid

import httpx
import pytest
from wolf_server.wazuh.capabilities import ACTION_ACTIVE_RESPONSE, CredentialCapabilities
from wolf_server.wazuh.config import WazuhConnection
from wolf_server.wazuh.server_api import WazuhActionNotPermittedError, WazuhServerApiActionClient


def _connection() -> WazuhConnection:
    return WazuhConnection(
        organization_id=uuid.uuid4(),
        opensearch_url="https://os.test:9200",
        opensearch_index_pattern="wazuh-alerts-*",
        opensearch_username="u",
        opensearch_password="p",  # noqa: S106
        server_api_url="https://api.test:55000",
        server_api_username="u",
        server_api_password="p",  # noqa: S106
        verify_tls=True,
    )


def _action_client(handler: httpx.MockTransport) -> WazuhServerApiActionClient:
    http = httpx.AsyncClient(base_url="https://api.test:55000", transport=handler, timeout=5.0)
    return WazuhServerApiActionClient(_connection(), client=http)


@pytest.mark.asyncio
async def test_active_response_refused_when_not_permitted() -> None:
    """Empty capabilities → fail closed, and NO request reaches the transport."""

    async def _never(_req: httpx.Request) -> httpx.Response:
        raise AssertionError("request must not be issued when capability is denied")

    client = _action_client(httpx.MockTransport(_never))
    caps = CredentialCapabilities(policies={})
    with pytest.raises(WazuhActionNotPermittedError, match="not authorized"):
        await client.execute_active_response(
            agent_id="001", command="firewall-drop", capabilities=caps
        )


@pytest.mark.asyncio
async def test_active_response_issues_put_when_permitted() -> None:
    seen: dict[str, object] = {}

    async def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/security/user/authenticate":
            return httpx.Response(200, json={"data": {"token": "jwt"}})
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["agents_list"] = request.url.params.get("agents_list")
        return httpx.Response(
            200, json={"data": {"affected_items": ["001"], "total_affected_items": 1}}
        )

    client = _action_client(httpx.MockTransport(_handler))
    caps = CredentialCapabilities(policies={ACTION_ACTIVE_RESPONSE: {"agent:id:*": "allow"}})
    body = await client.execute_active_response(
        agent_id="001", command="firewall-drop", capabilities=caps
    )
    assert seen == {"method": "PUT", "path": "/active-response", "agents_list": "001"}
    assert body["data"]["total_affected_items"] == 1
