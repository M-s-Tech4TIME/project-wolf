"""Resolve a organization's Wazuh connection by combining DB config + secrets backend.

This is the only sanctioned way to obtain a `WazuhConnection`.  It enforces:
  - Organization context is taken from the immutable OrganizationContext, never a parameter.
  - Credentials are fetched fresh from the secrets backend (no in-process cache).
  - Both DB-side config and secrets must exist; missing either fails closed.
"""

import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_common.errors import SecretNotFoundError, WolfError
from wolf_secrets.interface import SecretsBackend

from wolf_server.organization.context import OrganizationContext
from wolf_server.wazuh.config import WazuhConnection
from wolf_server.wazuh.models import OrganizationWazuhConfig


def opensearch_credential_key(organization_id: uuid.UUID) -> str:
    return f"wazuh.opensearch.{organization_id}"


def server_api_credential_key(organization_id: uuid.UUID) -> str:
    return f"wazuh.server_api.{organization_id}"


class WazuhConfigMissingError(WolfError):
    """No OrganizationWazuhConfig row exists for this organization."""

    http_status = 404
    error_code = "wazuh_config_missing"


async def get_wazuh_connection(
    ctx: OrganizationContext,
    db: AsyncSession,
    secrets: SecretsBackend,
) -> WazuhConnection:
    """Fetch and assemble the organization's Wazuh connection profile.

    Looks up the row in `organization_wazuh_configs`, then resolves the two
    credential keys against the secrets backend.  Returns a frozen
    `WazuhConnection` carrying the organization_id straight from the context.
    """
    row = await db.scalar(
        select(OrganizationWazuhConfig).where(
            OrganizationWazuhConfig.organization_id == ctx.organization_id
        )
    )
    if row is None:
        raise WazuhConfigMissingError(
            f"No Wazuh configuration for organization {ctx.organization_id}"
        )

    opensearch_creds = await _load_credential_blob(secrets, row.opensearch_credential_key)
    server_api_creds = await _load_credential_blob(secrets, row.server_api_credential_key)

    return WazuhConnection(
        organization_id=ctx.organization_id,
        opensearch_url=row.opensearch_url,
        opensearch_index_pattern=row.opensearch_index_pattern,
        opensearch_username=opensearch_creds["username"],
        opensearch_password=opensearch_creds["password"],
        server_api_url=row.server_api_url,
        server_api_username=server_api_creds["username"],
        server_api_password=server_api_creds["password"],
        verify_tls=row.verify_tls,
        inject_organization_filter=row.inject_organization_filter,
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
