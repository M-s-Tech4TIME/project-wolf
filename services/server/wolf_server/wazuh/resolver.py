"""Resolve a organization's Wazuh connection by combining DB config + secrets backend.

This is the only sanctioned way to obtain a `WazuhConnection`.  It enforces:
  - Organization context is taken from the immutable OrganizationContext, never a parameter.
  - Credentials are fetched fresh from the secrets backend (no in-process cache).
  - Both DB-side config and secrets must exist; missing either fails closed.

Phase 6.6-e (ADR 0020) — the **URLs come from the install-level ecosystem
topology**, fetched fresh per query; only the per-org *credentials* (+ index
pattern + the optional group-label filter) come from `organization_wazuh_configs`.
For a distributed deployment a **random** indexer node is chosen per query
(ADR 0020 decision 1 — even load spread across the cluster).  The per-org row
still carries legacy URL columns (written by the bootstrap CLI + the 6.6-c
credentials API) but they are NO LONGER read here — they are vestigial pending
a cleanup that also modernises the bootstrap CLI.
"""

import json
import random
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_common.errors import SecretNotFoundError, WolfError
from wolf_secrets.interface import SecretsBackend

from wolf_server.organization.context import OrganizationContext
from wolf_server.wazuh.config import WazuhConnection
from wolf_server.wazuh.models import OrganizationWazuhConfig, WazuhEcosystemTopology
from wolf_server.wazuh.topology import (
    WAZUH_TOPOLOGY_ADAPTER,
    DistributedTopology,
    SingleHostTopology,
)


def opensearch_credential_key(organization_id: uuid.UUID) -> str:
    return f"wazuh.opensearch.{organization_id}"


def server_api_credential_key(organization_id: uuid.UUID) -> str:
    return f"wazuh.server_api.{organization_id}"


class WazuhConfigMissingError(WolfError):
    """No OrganizationWazuhConfig row exists for this organization."""

    http_status = 404
    error_code = "wazuh_config_missing"


class WazuhTopologyMissingError(WolfError):
    """No install-level Wazuh ecosystem topology has been configured."""

    http_status = 404
    error_code = "wazuh_topology_missing"


def _resolve_runtime_endpoints(
    topology: WazuhEcosystemTopology,
) -> tuple[str, tuple[str, ...], str, bool]:
    """Return ``(indexer_url, indexer_fallback_urls, server_api_url, verify_tls)``.

    Single-host → the one indexer (no fallbacks) + manager.  Distributed → the
    indexer nodes **shuffled**: the first is the per-query random pick (load
    distribution, ADR 0020 decision 1) and the rest are ordered fallbacks the
    client retries on failure (decision 1's resilience half, Phase 6.6-g) + the
    manager master.  ``verify_tls`` is the install-wide topology setting.
    """
    shape = WAZUH_TOPOLOGY_ADAPTER.validate_python(topology.topology)
    if isinstance(shape, SingleHostTopology):
        return shape.indexer_url, (), shape.manager_url, topology.verify_tls
    if isinstance(shape, DistributedTopology):
        nodes = [node.url for node in shape.indexer_nodes]
        random.shuffle(nodes)  # noqa: S311 — load spread + fallback order, not security
        return nodes[0], tuple(nodes[1:]), shape.manager_master.url, topology.verify_tls
    raise ValueError(f"Unknown topology kind: {topology.kind!r}")  # pragma: no cover


async def get_wazuh_connection(
    ctx: OrganizationContext,
    db: AsyncSession,
    secrets: SecretsBackend,
) -> WazuhConnection:
    """Fetch and assemble the organization's Wazuh connection profile.

    Combines the install-level ecosystem topology (URLs + TLS posture, read
    fresh per query) with the per-org credential config (credential keys,
    index pattern, optional group-label filter).  Returns a frozen
    `WazuhConnection` carrying the organization_id straight from the context.
    """
    row = await db.scalar(
        select(OrganizationWazuhConfig).where(
            OrganizationWazuhConfig.organization_id == ctx.organization_id
        )
    )
    if row is None:
        raise WazuhConfigMissingError(
            f"No Wazuh credentials configured for organization {ctx.organization_id}"
        )

    topology = await db.scalar(select(WazuhEcosystemTopology))
    if topology is None:
        raise WazuhTopologyMissingError(
            "Wazuh ecosystem topology is not configured for this install "
            "(Settings → Wazuh Ecosystem)."
        )
    indexer_url, indexer_fallbacks, server_api_url, verify_tls = _resolve_runtime_endpoints(
        topology
    )

    opensearch_creds = await _load_credential_blob(secrets, row.opensearch_credential_key)
    server_api_creds = await _load_credential_blob(secrets, row.server_api_credential_key)

    return WazuhConnection(
        organization_id=ctx.organization_id,
        opensearch_url=indexer_url,
        opensearch_fallback_urls=indexer_fallbacks,
        opensearch_index_pattern=row.opensearch_index_pattern,
        opensearch_username=opensearch_creds["username"],
        opensearch_password=opensearch_creds["password"],
        server_api_url=server_api_url,
        server_api_username=server_api_creds["username"],
        server_api_password=server_api_creds["password"],
        verify_tls=verify_tls,
        inject_group_label_filter=row.inject_group_label_filter,
        agent_group_labels=tuple(row.agent_group_labels or ()),
    )


async def _load_credential_blob(secrets: SecretsBackend, key: str) -> dict[str, str]:
    """Fetch a JSON-encoded {username, password} blob from the secrets backend."""
    raw = await secrets.get(key)
    if raw is None:
        raise SecretNotFoundError(f"Missing Wazuh credential: {key!r}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SecretNotFoundError(f"Credential {key!r} is not valid JSON") from exc
    if not isinstance(data, dict) or "username" not in data or "password" not in data:
        raise SecretNotFoundError(
            f"Credential {key!r} missing required fields (username, password)"
        )
    return {"username": str(data["username"]), "password": str(data["password"])}
