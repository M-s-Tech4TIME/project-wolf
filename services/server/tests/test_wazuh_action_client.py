"""Bounded write surface — WazuhServerApiActionClient (Phase 6, ADR 0025).

The write client must (1) refuse an action the credential's RBAC forbids
BEFORE issuing any request (fail-closed), and (2) issue exactly the whitelisted
PUT /active-response when permitted.  Uses an httpx MockTransport — no real Wazuh.
"""

import json
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


def _never_transport() -> httpx.MockTransport:
    async def _never(_req: httpx.Request) -> httpx.Response:
        raise AssertionError("request must not be issued when capability is denied")

    return httpx.MockTransport(_never)


@pytest.mark.asyncio
async def test_active_response_refused_when_not_permitted() -> None:
    """Empty capabilities (the wolf-beta case) → fail closed, NO request issued."""
    client = _action_client(_never_transport())
    caps = CredentialCapabilities(policies={})
    with pytest.raises(WazuhActionNotPermittedError, match="not authorized"):
        await client.execute_active_response(
            agent_id="001", command="firewall-drop", capabilities=caps, agent_groups=["acme"]
        )


@pytest.mark.asyncio
async def test_active_response_refused_when_agent_not_in_granted_group() -> None:
    """Group-scoped grant, but the target agent is NOT in that group → refused
    before any request (cross-group denial)."""
    client = _action_client(_never_transport())
    caps = CredentialCapabilities(policies={ACTION_ACTIVE_RESPONSE: {"agent:group:acme": "allow"}})
    with pytest.raises(WazuhActionNotPermittedError, match="not authorized"):
        await client.execute_active_response(
            agent_id="009", command="firewall-drop", capabilities=caps, agent_groups=["beta"]
        )


def _permitted_handler(seen: dict[str, object]) -> httpx.MockTransport:
    async def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/security/user/authenticate":
            return httpx.Response(200, json={"data": {"token": "jwt"}})
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["agents_list"] = request.url.params.get("agents_list")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"data": {"affected_items": ["001"], "total_affected_items": 1}}
        )

    return httpx.MockTransport(_handler)


@pytest.mark.asyncio
async def test_active_response_issues_put_when_permitted_by_agent_id() -> None:
    seen: dict[str, object] = {}
    client = _action_client(_permitted_handler(seen))
    caps = CredentialCapabilities(policies={ACTION_ACTIVE_RESPONSE: {"agent:id:*": "allow"}})
    body = await client.execute_active_response(
        agent_id="001",
        command="firewall-drop",
        capabilities=caps,
        agent_groups=[],
        srcip="203.0.113.7",
    )
    assert seen["method"] == "PUT"
    assert seen["path"] == "/active-response"
    assert seen["agents_list"] == "001"
    # The corrected 4.14.3 body: !-prefixed command, srcip in alert.data, NO custom.
    sent = seen["body"]
    assert isinstance(sent, dict)
    assert sent["command"] == "!firewall-drop"
    assert "custom" not in sent
    assert sent["alert"] == {"data": {"srcip": "203.0.113.7"}}
    assert body["data"]["total_affected_items"] == 1


@pytest.mark.asyncio
async def test_active_response_issues_put_when_permitted_by_group() -> None:
    """The real per-org case: AR granted on agent:group:acme, target IS in acme
    → the PUT is issued (the 6-a.1 fix unblocks per-org execution)."""
    seen: dict[str, object] = {}
    client = _action_client(_permitted_handler(seen))
    caps = CredentialCapabilities(policies={ACTION_ACTIVE_RESPONSE: {"agent:group:acme": "allow"}})
    body = await client.execute_active_response(
        agent_id="002",
        command="firewall-drop",
        capabilities=caps,
        agent_groups=["default", "acme"],
    )
    assert seen["method"] == "PUT"
    assert seen["agents_list"] == "002"
    assert body["data"]["total_affected_items"] == 1
