"""Resolved Wazuh connection profile — what the clients are constructed with.

Built by `resolver.get_wazuh_connection()` per request.  Never cached across
organizations — fetched fresh each time to avoid the connection-pool bleed risk
from doc 05.
"""

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class WazuhConnection:
    """Resolved, ready-to-use Wazuh connection profile.

    Frozen so a downstream caller cannot accidentally swap the organization out.
    Created by `resolver.get_wazuh_connection()` and passed to the clients.
    """

    organization_id: uuid.UUID
    opensearch_url: str
    opensearch_index_pattern: str
    opensearch_username: str
    opensearch_password: str
    server_api_url: str
    server_api_username: str
    server_api_password: str
    verify_tls: bool
    # When True, inject a `terms:{agent.labels.group:[...]}` clause (the real
    # Wazuh field) into every indexer query, scoped to `agent_group_labels`.
    # Default False — the per-org credential's own Wazuh RBAC/DLS is the
    # isolation boundary (Phase 6.6-f, ADR 0020).
    inject_group_label_filter: bool = False
    agent_group_labels: tuple[str, ...] = ()
    # Other indexer nodes to retry, in order, if `opensearch_url` fails
    # (distributed topology; empty for single-host). Phase 6.6-g — ADR 0020
    # decision 1's resilience half (random primary + fallback-on-failure).
    opensearch_fallback_urls: tuple[str, ...] = ()
