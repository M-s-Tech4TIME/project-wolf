"""Per-org Wazuh credential probing + topology endpoint resolution — 6.6-c.

The per-org layer (ADR 0020) holds the *credentials* an organization uses to
query the install's Wazuh ecosystem; the *URLs* come from the install-level
topology (6.6-a).  This module bridges the two:

  - :func:`resolve_endpoints_from_topology` derives the indexer + manager
    (Server API) URLs an org should query from the configured install
    topology (random-node selection for distributed deployments is deferred
    to the runtime path in 6.6-e — here we deterministically pick the first
    indexer node + the manager master, which is all the credential probe
    needs).
  - :func:`probe_org_credentials` verifies an org's credentials against those
    URLs and returns a **scope summary** (how many agents / groups the
    Server API credential can see), per ADR 0020's "Test credentials"
    contract.  It never raises — the caller (a soft-fail save) records the
    outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from wolf_server.wazuh.probe import (
    EndpointProbeResult,
    probe_indexer_read,
    probe_manager_api,
)
from wolf_server.wazuh.topology import (
    WAZUH_TOPOLOGY_ADAPTER,
    DistributedTopology,
    SingleHostTopology,
)

if TYPE_CHECKING:
    from wolf_server.wazuh.models import WazuhEcosystemTopology

_TIMEOUT = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=10.0)


@dataclass(frozen=True)
class IndexAccessResult:
    """Whether the indexer credential can read one configured index pattern."""

    pattern: str
    ok: bool
    detail: str
    status_code: int | None = None


@dataclass(frozen=True)
class OrgCredentialProbeResult:
    """Outcome of probing one org's Wazuh credentials against the topology."""

    indexer: EndpointProbeResult
    manager: EndpointProbeResult
    agent_count: int | None
    group_count: int | None
    groups: list[str] | None
    scope_detail: str
    # Per-pattern indexer read check (one entry per configured index pattern).
    index_results: list[IndexAccessResult]

    @property
    def ok(self) -> bool:
        """Both credential backends authenticated (scope is best-effort extra)."""
        return self.indexer.ok and self.manager.ok


def resolve_endpoints_from_topology(row: WazuhEcosystemTopology) -> tuple[str, str, bool]:
    """Return ``(indexer_url, manager_url, verify_tls)`` for an install topology.

    Distributed deployments deterministically pick the first indexer node +
    the manager master here; per-query random indexer routing lands in 6.6-e.
    """
    shape = WAZUH_TOPOLOGY_ADAPTER.validate_python(row.topology)
    if isinstance(shape, SingleHostTopology):
        return shape.indexer_url, shape.manager_url, row.verify_tls
    if isinstance(shape, DistributedTopology):
        return shape.indexer_nodes[0].url, shape.manager_master.url, row.verify_tls
    raise ValueError(f"Unknown topology kind: {row.kind!r}")  # pragma: no cover


def _total_affected(response: httpx.Response) -> int | None:
    """Pull ``data.total_affected_items`` from a Wazuh Server API response."""
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    inner = data.get("data")
    if not isinstance(inner, dict):
        return None
    total = inner.get("total_affected_items")
    return int(total) if isinstance(total, int) else None


_AGENT_GROUP_PREFIX = "agent:group:"
_UNRESTRICTED_RESOURCES = frozenset({"agent:group:*", "agent:id:*", "*:*:*"})


def _scoped_groups_from_policies(response: httpx.Response) -> tuple[list[str] | None, bool]:
    """Parse ``GET /security/users/me/policies`` → ``(groups, unrestricted)``.

    The endpoint returns the *current* user's effective, processed policies as
    ``data = {action: {resource: effect}}`` and needs no special permission (a
    correctly-scoped per-org user CAN call it about itself).  We read the
    ``agent:read`` action's allowed resources: ``agent:group:<name>`` entries
    are the groups the credential is genuinely SCOPED to — the authoritative
    answer, NOT the incidental multi-group membership of the agents it happens
    to see.  A credential scoped to several groups yields several names.
    ``agent:group:*`` / ``agent:id:*`` / ``*:*:*`` means it is not restricted to
    specific groups (``unrestricted=True``).  Any parse failure → ``(None, False)``.
    """
    try:
        data = response.json()
    except ValueError:
        return None, False
    if not isinstance(data, dict):
        return None, False
    inner = data.get("data")
    if not isinstance(inner, dict):
        return None, False
    res_map = inner.get("agent:read")
    if not isinstance(res_map, dict):
        return None, False
    groups: set[str] = set()
    unrestricted = False
    for resource, effect in res_map.items():
        if effect != "allow" or not isinstance(resource, str):
            continue
        if resource in _UNRESTRICTED_RESOURCES:
            unrestricted = True
        elif resource.startswith(_AGENT_GROUP_PREFIX):
            name = resource[len(_AGENT_GROUP_PREFIX) :]
            if name and name != "*":
                groups.add(name)
    return sorted(groups), unrestricted


async def _fetch_scope(
    client: httpx.AsyncClient,
    server_api_url: str,
    username: str,
    password: str,
) -> tuple[int | None, int | None, list[str] | None, str]:
    """Best-effort agent count + the groups the Server API credential is SCOPED to.

    Authenticates for a JWT, reads the visible agent count from ``/agents``
    (``total_affected_items``), and the *true* group scope from
    ``/security/users/me/policies`` (see :func:`_scoped_groups_from_policies`).
    Degrades gracefully — any failure yields ``(None, None, None, <reason>)``.
    """
    base = server_api_url.rstrip("/")
    try:
        auth = await client.post(base + "/security/user/authenticate", auth=(username, password))
        if auth.status_code != 200:
            return None, None, None, "Scope unavailable — Server API authentication failed."
        token = auth.json().get("data", {}).get("token")
        if not token:
            return None, None, None, "Scope unavailable — no token issued."
        headers = {"Authorization": f"Bearer {token}"}
        agents = await client.get(base + "/agents", params={"limit": 1}, headers=headers)
        policies = await client.get(base + "/security/users/me/policies", headers=headers)
    except httpx.RequestError:
        return None, None, None, "Scope unavailable — Server API unreachable while reading scope."

    agent_count = _total_affected(agents)
    groups, unrestricted = _scoped_groups_from_policies(policies)
    agents_txt = (
        f"{agent_count} agent(s)" if agent_count is not None else "an unknown number of agents"
    )
    if unrestricted:
        # Not restricted to specific groups — a specific count would understate.
        return (
            agent_count,
            None,
            None,
            f"Credential sees {agents_txt}, not restricted to specific agent groups.",
        )
    if groups:
        shown = ", ".join(groups[:10])
        more = "" if len(groups) <= 10 else f" (+{len(groups) - 10} more)"
        scope_txt = f"scoped to {len(groups)} group(s): {shown}{more}"
        return agent_count, len(groups), groups, f"Credential sees {agents_txt}, {scope_txt}."
    if agent_count is None and groups is None:
        return None, None, None, "Authenticated, but the credential scope could not be read."
    # Authenticated, agent:read present but no agent:group:* resource resolved.
    return agent_count, 0, [], f"Credential sees {agents_txt}; no agent-group scope detected."


async def _probe_indexes(
    client: httpx.AsyncClient,
    indexer_url: str,
    indexer_user: str,
    indexer_password: str,
    index_patterns: list[str],
    *,
    verify_tls: bool,
    group_labels: list[str] | None = None,
) -> tuple[EndpointProbeResult, list[IndexAccessResult]]:
    """Check read access to EACH configured index pattern, plus an overall verdict.

    Each pattern gets its own ``_count`` probe (Phase 6.6-f follow-up) so the UI
    can show a per-index pass/fail. When ``group_labels`` is set (the opt-in
    filter is on), each count is taken THROUGH that ``agent.labels.group`` filter
    so the reported doc counts are what Wolf would actually see. A 401/transport
    failure on any pattern means the credential itself is bad/unreachable — we
    stop and surface that as the overall verdict. Otherwise the overall is
    ``ok`` only when EVERY configured pattern is readable.
    """
    patterns = index_patterns or ["wazuh-alerts-*"]
    index_results: list[IndexAccessResult] = []
    auth_or_transport_failure = False
    for pattern in patterns:
        r = await probe_indexer_read(
            indexer_url, indexer_user, indexer_password, pattern,
            verify_tls=verify_tls, client=client, group_labels=group_labels,
        )
        index_results.append(
            IndexAccessResult(pattern=pattern, ok=r.ok, detail=r.detail, status_code=r.status_code)
        )
        # 401 (bad creds) or transport error (None) is a credential-level fault,
        # not an index-level one — no point probing the remaining patterns.
        if r.status_code == 401 or r.status_code is None:
            auth_or_transport_failure = True
            break

    if auth_or_transport_failure:
        last = index_results[-1]
        overall = EndpointProbeResult(
            role="indexer", url=indexer_url, ok=False,
            status_code=last.status_code, detail=last.detail,
        )
    elif len(index_results) == 1:
        # Single pattern (the common case): the overall verdict IS that pattern's
        # result — preserve its real status code + detail.
        only = index_results[0]
        overall = EndpointProbeResult(
            role="indexer", url=indexer_url, ok=only.ok,
            status_code=only.status_code, detail=only.detail,
        )
    elif all(r.ok for r in index_results):
        n = len(index_results)
        detail = (
            index_results[0].detail
            if n == 1
            else f"Credential can read all {n} configured index pattern(s)."
        )
        overall = EndpointProbeResult(
            role="indexer", url=indexer_url, ok=True, status_code=200, detail=detail,
        )
    else:
        bad = [r.pattern for r in index_results if not r.ok]
        overall = EndpointProbeResult(
            role="indexer", url=indexer_url, ok=False, status_code=200,
            detail=(
                f"Credential cannot read {len(bad)} of {len(index_results)} "
                f"index pattern(s): {', '.join(bad)}."
            ),
        )
    return overall, index_results


async def probe_org_credentials(
    *,
    indexer_url: str,
    indexer_user: str,
    indexer_password: str,
    index_patterns: list[str],
    server_api_url: str,
    server_api_user: str,
    server_api_password: str,
    verify_tls: bool,
    inject_group_label_filter: bool = False,
    agent_group_labels: list[str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> OrgCredentialProbeResult:
    """Probe an org's Indexer + Server API credentials and summarise scope.

    The indexer leg tests **index read** (``_count``) against EACH configured
    index pattern — the access the per-org credential actually exists to have,
    not cluster root — so a correctly-scoped credential is reported ``ok``
    instead of a misleading 403, and the caller learns per-index whether the
    credential can reach each pattern (Phase 6.6-f + follow-up).

    When ``inject_group_label_filter`` is on (with labels), the per-index counts
    are taken THROUGH the same ``agent.labels.group`` filter Wolf injects at
    query time, so the probe shows the *effective* (scoped) view — matching what
    the org's users will actually see.
    """
    group_labels = (
        list(agent_group_labels)
        if (inject_group_label_filter and agent_group_labels)
        else None
    )
    owns_client = client is None
    client = client or httpx.AsyncClient(verify=verify_tls, timeout=_TIMEOUT)
    try:
        indexer, index_results = await _probe_indexes(
            client, indexer_url, indexer_user, indexer_password, index_patterns,
            verify_tls=verify_tls, group_labels=group_labels,
        )
        manager = await probe_manager_api(
            server_api_url, server_api_user, server_api_password,
            verify_tls=verify_tls, client=client,
        )
        if manager.ok:
            agent_count, group_count, groups, scope_detail = await _fetch_scope(
                client, server_api_url, server_api_user, server_api_password
            )
        else:
            agent_count, group_count, groups = None, None, None
            scope_detail = "Scope unavailable — Server API credentials failed to authenticate."
    finally:
        if owns_client:
            await client.aclose()

    return OrgCredentialProbeResult(
        indexer=indexer,
        manager=manager,
        agent_count=agent_count,
        group_count=group_count,
        groups=groups,
        scope_detail=scope_detail,
        index_results=index_results,
    )
