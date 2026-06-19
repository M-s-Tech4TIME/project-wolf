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
    resolve_agent_groups,
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


# ── can_on_agent: Wazuh RBAC agent resource expansion (id OR group) ──────────


def test_can_on_agent_allows_via_group_membership() -> None:
    """A per-org credential grants AR by GROUP; an id-targeted action on an
    agent IN that group must be allowed (the 6-a.1 fix)."""
    caps = _caps({ACTION_ACTIVE_RESPONSE: {"agent:group:acme": "allow"}})
    # Agent 002 is in group acme → allowed via group expansion …
    assert caps.can_on_agent(ACTION_ACTIVE_RESPONSE, "002", ["default", "acme"]) is True
    # … but plain can() (id-only) can't see it — proves the expansion is needed.
    assert caps.can(ACTION_ACTIVE_RESPONSE, "agent:id:002") is False


def test_can_on_agent_denies_when_agent_not_in_granted_group() -> None:
    """Cross-group: an agent NOT in the granted group is refused."""
    caps = _caps({ACTION_ACTIVE_RESPONSE: {"agent:group:acme": "allow"}})
    assert caps.can_on_agent(ACTION_ACTIVE_RESPONSE, "009", ["default", "beta"]) is False


def test_can_on_agent_allows_via_agent_id_wildcard() -> None:
    """A broad credential granted agent:id:* is allowed with no groups at all
    (single-org parity)."""
    caps = _caps({ACTION_ACTIVE_RESPONSE: {"agent:id:*": "allow"}})
    assert caps.can_on_agent(ACTION_ACTIVE_RESPONSE, "002", []) is True


def test_can_on_agent_deny_wins_across_candidates() -> None:
    """An explicit deny on the agent id wins over a group allow (deny-wins
    across the whole id+group candidate set)."""
    caps = _caps(
        {ACTION_ACTIVE_RESPONSE: {"agent:group:acme": "allow", "agent:id:002": "deny"}}
    )
    assert caps.can_on_agent(ACTION_ACTIVE_RESPONSE, "002", ["acme"]) is False
    # A different acme agent with no explicit deny is still allowed.
    assert caps.can_on_agent(ACTION_ACTIVE_RESPONSE, "003", ["acme"]) is True


def test_can_on_agent_fail_closed_no_capability() -> None:
    """No active-response grant at all → refused regardless of groups (the
    wolf-beta case)."""
    caps = _caps({"agent:read": {"agent:group:acme": "allow"}})
    assert caps.can_on_agent(ACTION_ACTIVE_RESPONSE, "002", ["acme"]) is False
    assert _caps({}).can_on_agent(ACTION_ACTIVE_RESPONSE, "002", ["acme"]) is False


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


# ── resolve_agent_groups: live group lookup for the capability check ─────────


@pytest.mark.asyncio
async def test_resolve_agent_groups_returns_memberships() -> None:
    payload = {
        "data": {
            "affected_items": [{"id": "002", "group": ["default", "acme"]}],
            "total_affected_items": 1,
        }
    }
    groups = await resolve_agent_groups(_StubServerApi(payload), "002")
    assert groups == ["default", "acme"]


@pytest.mark.asyncio
async def test_resolve_agent_groups_fail_closed_on_error() -> None:
    groups = await resolve_agent_groups(_StubServerApi(None, raises=True), "002")
    assert groups == []


@pytest.mark.asyncio
async def test_resolve_agent_groups_fail_closed_on_empty_or_malformed() -> None:
    # No affected items (agent not visible to the credential).
    empty = {"data": {"affected_items": [], "total_affected_items": 0}}
    assert await resolve_agent_groups(_StubServerApi(empty), "002") == []
    # Malformed shape.
    assert await resolve_agent_groups(_StubServerApi({"nope": 1}), "002") == []
