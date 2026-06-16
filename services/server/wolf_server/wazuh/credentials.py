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
    probe_indexer,
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
class OrgCredentialProbeResult:
    """Outcome of probing one org's Wazuh credentials against the topology."""

    indexer: EndpointProbeResult
    manager: EndpointProbeResult
    agent_count: int | None
    group_count: int | None
    scope_detail: str

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
        return shape.indexer_nodes[0].url, shape.manager_master_url, row.verify_tls
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


async def _fetch_scope(
    client: httpx.AsyncClient,
    server_api_url: str,
    username: str,
    password: str,
) -> tuple[int | None, int | None, str]:
    """Best-effort agent/group counts the Server API credential can see.

    Authenticates for a JWT, then queries ``/agents`` and ``/groups`` with a
    ``limit=1`` so we read ``total_affected_items`` without pulling data.
    Degrades gracefully — any failure yields ``(None, None, <reason>)``.
    """
    base = server_api_url.rstrip("/")
    try:
        auth = await client.post(base + "/security/user/authenticate", auth=(username, password))
        if auth.status_code != 200:
            return None, None, "Scope unavailable — Server API authentication failed."
        token = auth.json().get("data", {}).get("token")
        if not token:
            return None, None, "Scope unavailable — no token issued."
        headers = {"Authorization": f"Bearer {token}"}
        agents = await client.get(base + "/agents", params={"limit": 1}, headers=headers)
        groups = await client.get(base + "/groups", params={"limit": 1}, headers=headers)
    except httpx.RequestError:
        return None, None, "Scope unavailable — Server API unreachable while reading scope."

    agent_count = _total_affected(agents)
    group_count = _total_affected(groups)
    if agent_count is None and group_count is None:
        return None, None, "Authenticated, but scope (agents/groups) could not be read."
    agents_txt = (
        f"{agent_count} agent(s)" if agent_count is not None else "an unknown number of agents"
    )
    groups_txt = (
        f"{group_count} group(s)" if group_count is not None else "an unknown number of groups"
    )
    return agent_count, group_count, f"Credential sees {agents_txt} across {groups_txt}."


async def probe_org_credentials(
    *,
    indexer_url: str,
    indexer_user: str,
    indexer_password: str,
    server_api_url: str,
    server_api_user: str,
    server_api_password: str,
    verify_tls: bool,
    client: httpx.AsyncClient | None = None,
) -> OrgCredentialProbeResult:
    """Probe an org's Indexer + Server API credentials and summarise scope."""
    owns_client = client is None
    client = client or httpx.AsyncClient(verify=verify_tls, timeout=_TIMEOUT)
    try:
        indexer = await probe_indexer(
            indexer_url, indexer_user, indexer_password, verify_tls=verify_tls, client=client
        )
        manager = await probe_manager_api(
            server_api_url, server_api_user, server_api_password,
            verify_tls=verify_tls, client=client,
        )
        if manager.ok:
            agent_count, group_count, scope_detail = await _fetch_scope(
                client, server_api_url, server_api_user, server_api_password
            )
        else:
            agent_count, group_count = None, None
            scope_detail = "Scope unavailable — Server API credentials failed to authenticate."
    finally:
        if owns_client:
            await client.aclose()

    return OrgCredentialProbeResult(
        indexer=indexer,
        manager=manager,
        agent_count=agent_count,
        group_count=group_count,
        scope_detail=scope_detail,
    )
