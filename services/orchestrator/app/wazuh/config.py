"""Resolved Wazuh connection profile — what the clients are constructed with.

Built by `resolver.get_wazuh_connection()` per request.  Never cached across
tenants — fetched fresh each time to avoid the connection-pool bleed risk
from doc 05.
"""

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class WazuhConnection:
    """Resolved, ready-to-use Wazuh connection profile.

    Frozen so a downstream caller cannot accidentally swap the tenant out.
    Created by `resolver.get_wazuh_connection()` and passed to the clients.
    """

    tenant_id: uuid.UUID
    opensearch_url: str
    opensearch_index_pattern: str
    opensearch_username: str
    opensearch_password: str
    server_api_url: str
    server_api_username: str
    server_api_password: str
    verify_tls: bool
