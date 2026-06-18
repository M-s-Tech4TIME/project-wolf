"""Capability introspection — Phase 6 (ADR 0025).

Wolf must know what the per-org Wazuh credential's RBAC permits before it
offers/executes a write, and must fail closed when it can't read that.
"""

from typing import Any

import pytest
from wolf_server.wazuh.capabilities import (
    ACTION_ACTIVE_RESPONSE,
    CredentialCapabilities,
    fetch_credential_capabilities,
)


def _caps(policies: dict[str, dict[str, str]]) -> CredentialCapabilities:
    return CredentialCapabilities(policies=policies)


def test_can_allows_matching_wildcard_resource() -> None:
    caps = _caps({ACTION_ACTIVE_RESPONSE: {"agent:id:*": "allow"}})
    assert caps.can(ACTION_ACTIVE_RESPONSE, "agent:id:001") is True


def test_can_allows_full_wildcard() -> None:
    caps = _caps({ACTION_ACTIVE_RESPONSE: {"*:*:*": "allow"}})
    assert caps.can(ACTION_ACTIVE_RESPONSE, "agent:id:042") is True


def test_deny_overrides_allow() -> None:
    caps = _caps(
        {ACTION_ACTIVE_RESPONSE: {"agent:id:*": "allow", "agent:id:001": "deny"}}
    )
    assert caps.can(ACTION_ACTIVE_RESPONSE, "agent:id:001") is False
    assert caps.can(ACTION_ACTIVE_RESPONSE, "agent:id:002") is True


def test_can_false_for_unknown_action_or_no_match() -> None:
    caps = _caps({ACTION_ACTIVE_RESPONSE: {"agent:group:acme": "allow"}})
    # Different action entirely.
    assert caps.can("agent:restart", "agent:id:001") is False
    # Right action, but the resource type doesn't match (group vs id).
    assert caps.can(ACTION_ACTIVE_RESPONSE, "agent:id:001") is False


def test_can_false_on_empty_policies_fail_closed() -> None:
    assert _caps({}).can(ACTION_ACTIVE_RESPONSE, "agent:id:001") is False


def test_available_actions_and_action_classes() -> None:
    caps = _caps(
        {
            ACTION_ACTIVE_RESPONSE: {"agent:id:*": "allow"},
            "agent:read": {"agent:group:acme": "allow"},
            "nothing:allowed": {"x:y:z": "deny"},
        }
    )
    assert caps.available_actions() == {ACTION_ACTIVE_RESPONSE, "agent:read"}
    assert caps.available_action_classes() == {"active_response"}


class _StubServerApi:
    def __init__(self, payload: Any, *, raises: bool = False) -> None:
        self._payload = payload
        self._raises = raises

    async def get(self, _path: str, *, params: Any = None) -> Any:
        if self._raises:
            raise RuntimeError("server api unreachable")
        return self._payload


@pytest.mark.asyncio
async def test_fetch_parses_effective_policies() -> None:
    payload = {"data": {ACTION_ACTIVE_RESPONSE: {"agent:id:*": "allow"}}}
    caps = await fetch_credential_capabilities(_StubServerApi(payload))
    assert caps.can(ACTION_ACTIVE_RESPONSE, "agent:id:003") is True


@pytest.mark.asyncio
async def test_fetch_fail_closed_on_error() -> None:
    caps = await fetch_credential_capabilities(_StubServerApi(None, raises=True))
    assert caps.policies == {}
    assert caps.can(ACTION_ACTIVE_RESPONSE, "agent:id:001") is False


@pytest.mark.asyncio
async def test_fetch_fail_closed_on_malformed_payload() -> None:
    caps = await fetch_credential_capabilities(_StubServerApi({"unexpected": "shape"}))
    assert caps.policies == {}
