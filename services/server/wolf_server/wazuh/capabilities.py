"""Wazuh credential capability introspection — Phase 6 (ADR 0025).

Wolf is capability-driven: it acts only within what the per-org Wazuh
credential's RBAC authorizes (`wolf-unrestricted-full-power`).  This module
reads a credential's effective policies via ``GET /security/users/me/policies``
(allowed for self — no special permission, the same endpoint 6.6-f uses for
scope) and answers *"is this credential allowed <action> on <resource>?"* so
Wolf offers + executes only authorized actions.

Fail-closed by design: any parse/transport failure yields an empty policy map,
so :meth:`CredentialCapabilities.can` returns ``False`` — Wolf never offers a
write it could not confirm the credential is permitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

# Wazuh RBAC actions Wolf maps to its own action classes.
ACTION_ACTIVE_RESPONSE = "active-response:command"  # run an AR command on agents
ACTION_MODIFY_GROUP = "agent:modify_group"  # add/remove an agent to/from a group (6-e.2)

# Map Wolf action class → the SET of Wazuh RBAC actions that gate it (ADR 0029):
# Wolf offers the class if the credential holds ANY of them.  Extended as more
# action classes land (rule_tuning, config_change).
WOLF_ACTION_CLASS_RBAC: dict[str, frozenset[str]] = {
    "active_response": frozenset({ACTION_ACTIVE_RESPONSE}),
    "agent_action": frozenset({ACTION_MODIFY_GROUP}),
}

_ALLOW = "allow"
_DENY = "deny"


def _resource_matches(pattern: str, resource: str) -> bool:
    """Segment-wise wildcard match of a Wazuh resource string.

    Wazuh resources are ``type:field:value`` triples (``agent:id:001``,
    ``agent:group:acme``); ``*`` matches any single segment and the bare
    ``*`` / ``*:*:*`` match everything.  Matching is arity-exact otherwise.
    """
    if pattern in ("*", "*:*:*"):
        return True
    pat = pattern.split(":")
    res = resource.split(":")
    if len(pat) != len(res):
        return False
    return all(p == "*" or p == r for p, r in zip(pat, res, strict=True))


@dataclass(frozen=True)
class CredentialCapabilities:
    """A Wazuh credential's effective ``action → {resource: effect}`` policy map."""

    # action -> {resource_pattern -> "allow" | "deny"}
    policies: dict[str, dict[str, str]]

    def can(self, action: str, resource: str) -> bool:
        """True iff the credential is allowed ``action`` on ``resource``.

        Explicit ``deny`` on a matching resource wins over ``allow`` (Wazuh
        RBAC semantics).  Unknown action or no matching allow → ``False``
        (fail-closed).
        """
        res_map = self.policies.get(action)
        if not res_map:
            return False
        allowed = False
        for pattern, effect in res_map.items():
            if _resource_matches(pattern, resource):
                if effect == _DENY:
                    return False
                if effect == _ALLOW:
                    allowed = True
        return allowed

    def can_on_agent(
        self, action: str, agent_id: str, agent_groups: Iterable[str]
    ) -> bool:
        """True iff the credential is allowed ``action`` on the given agent.

        Mirrors how Wazuh RBAC actually evaluates an agent-targeted action: it
        is authorized on ``agent:id:<id>`` (or a matching wildcard) **OR** on
        ``agent:group:<g>`` for ANY group the agent belongs to.  A per-org
        credential grants by group (e.g. ``active-response:command`` on
        ``agent:group:acme``), so an id-only check would falsely refuse every
        agent it is genuinely authorized for (6.6-f isolation model).

        Deny-wins across the WHOLE candidate set: an explicit ``deny`` on any
        matching resource (id or group) refuses, even if another grants allow.
        Unknown action / no matching allow → ``False`` (fail-closed).
        """
        res_map = self.policies.get(action)
        if not res_map:
            return False
        candidates = [f"agent:id:{agent_id}", *(f"agent:group:{g}" for g in agent_groups)]
        allowed = False
        for resource in candidates:
            for pattern, effect in res_map.items():
                if _resource_matches(pattern, resource):
                    if effect == _DENY:
                        return False
                    if effect == _ALLOW:
                        allowed = True
        return allowed

    def available_actions(self) -> set[str]:
        """Actions the credential is allowed on at least one resource."""
        return {
            action
            for action, res_map in self.policies.items()
            if any(effect == _ALLOW for effect in res_map.values())
        }

    def available_action_classes(self) -> set[str]:
        """Wolf action classes this credential is RBAC-permitted to perform.

        The intersection of Wolf's known action classes with the credential's
        allowed Wazuh actions — what Wolf may even *offer*.  Resource-level
        gating (which agent/group) is re-checked per proposal at execution.
        """
        actions = self.available_actions()
        return {
            wolf_class
            for wolf_class, rbac_actions in WOLF_ACTION_CLASS_RBAC.items()
            if actions & rbac_actions
        }


def _parse_policies(payload: Any) -> dict[str, dict[str, str]]:
    """Parse ``GET /security/users/me/policies`` → ``{action: {resource: effect}}``.

    The endpoint returns the current user's effective, processed policies as
    ``data = {action: {resource: effect}}``.  Anything malformed is dropped;
    a fully unparseable payload yields ``{}`` (fail-closed)."""
    if not isinstance(payload, dict):
        return {}
    inner = payload.get("data")
    if not isinstance(inner, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for action, res_map in inner.items():
        if not isinstance(action, str) or not isinstance(res_map, dict):
            continue
        clean: dict[str, str] = {}
        for resource, effect in res_map.items():
            if isinstance(resource, str) and isinstance(effect, str):
                clean[resource] = effect
        if clean:
            out[action] = clean
    return out


async def fetch_credential_capabilities(server_api: Any) -> CredentialCapabilities:
    """Read a credential's effective RBAC policies via a read-only Server-API client.

    ``server_api`` is a :class:`~wolf_server.wazuh.server_api.WazuhServerApiClient`
    (only its read-only ``get`` is used).  Any failure → empty capabilities
    (fail-closed: Wolf offers no writes it can't confirm).
    """
    try:
        payload = await server_api.get("/security/users/me/policies")
    except Exception:  # noqa: BLE001 — fail-closed on any introspection failure
        return CredentialCapabilities(policies={})
    return CredentialCapabilities(policies=_parse_policies(payload))


async def resolve_agent_groups(server_api: Any, agent_id: str) -> list[str]:
    """Resolve an agent's current group memberships (read-only) for a capability check.

    Wazuh authorizes an agent action on ``agent:id:<id>`` OR on
    ``agent:group:<g>`` for any group the agent is in, so
    :meth:`CredentialCapabilities.can_on_agent` needs the agent's live groups —
    resolved *fresh* at decision time (a stale proposal could name an agent
    whose membership has since changed).  ``server_api`` is the read-only
    :class:`~wolf_server.wazuh.server_api.WazuhServerApiClient`.

    Any failure / unexpected shape → ``[]`` (fail-closed: an unknown group can
    never broaden the grant)."""
    try:
        payload = await server_api.get(
            "/agents", params={"agents_list": agent_id, "select": "group"}
        )
    except Exception:  # noqa: BLE001 — fail-closed on any read failure
        return []
    if not isinstance(payload, dict):
        return []
    items = payload.get("data", {}).get("affected_items", [])
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return []
    groups = items[0].get("group", [])
    if not isinstance(groups, list):
        return []
    return [str(g) for g in groups]


async def resolve_agent_os(server_api: Any, agent_id: str) -> str | None:
    """Resolve an agent's OS signal string (read-only) for platform-fit checks.

    Returns a raw signal blob (``os.platform``/``os.uname``/``os.name`` joined)
    that :func:`wolf_server.wazuh.active_response.classify_os` maps to an OS
    class; ``None`` on any failure / unknown (caller must NOT hard-gate on
    ``None`` — fail-open, the credential + approver remain the backstops)."""
    try:
        payload = await server_api.get(
            "/agents", params={"agents_list": agent_id, "select": "os.platform,os.uname,os.name"}
        )
    except Exception:  # noqa: BLE001 — fail-open: unknown OS must not block a write
        return None
    if not isinstance(payload, dict):
        return None
    items = payload.get("data", {}).get("affected_items", [])
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return None
    os_info = items[0].get("os", {})
    if not isinstance(os_info, dict):
        return None
    parts = [os_info.get("platform"), os_info.get("uname"), os_info.get("name")]
    blob = " ".join(str(p) for p in parts if p)
    return blob or None
