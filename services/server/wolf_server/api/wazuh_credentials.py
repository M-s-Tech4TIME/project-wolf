"""Per-org Wazuh credentials API — Phase 6.6-c, ADR 0020.

GET  /api/v1/superuser/organizations/{id}/wazuh-credentials
    Return the org's Wazuh credential configuration (usernames, index pattern,
    group-label filter, validation timestamp).  Passwords are never returned.

PUT  /api/v1/superuser/organizations/{id}/wazuh-credentials
    Configure / rotate the org's Wazuh credentials.  **Soft fail** (ADR 0020
    decision 3): the save SUCCEEDS even if the probe fails — the Superuser may
    save now and the Wazuh-side admin provisions the user later.  ``validated_at``
    is stamped only on a successful probe; the response carries per-endpoint
    probe results, a scope summary, and a warning when the probe failed.

Both routes are **Superuser-only** (ADR 0018 + ADR 0020 concentrate ALL Wazuh
configuration in the Superuser — an org Admin/Engineer is rejected at the
``require_superuser`` dependency).  Credentials live in the secrets backend,
keyed per-org exactly as the runtime resolver reads them; the URLs come from
the install-level ecosystem topology (6.6-a) — so a PUT requires a configured
topology (409 otherwise).
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wolf_server.api.superuser import require_superuser
from wolf_server.audit.log import write_event
from wolf_server.audit.models import AuditEvent
from wolf_server.database import get_db
from wolf_server.organization.models import Organization, User
from wolf_server.secrets_factory import get_secrets_backend
from wolf_server.wazuh.credentials import probe_org_credentials, resolve_endpoints_from_topology
from wolf_server.wazuh.models import OrganizationWazuhConfig, WazuhEcosystemTopology
from wolf_server.wazuh.resolver import opensearch_credential_key, server_api_credential_key

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/superuser/organizations", tags=["superuser-wazuh"])

_DEFAULT_INDEX_FILTER = "wazuh-alerts-*"


def _clean_labels(labels: list[str] | None) -> list[str]:
    """Trim, drop blanks, and de-dupe (order-preserving) group labels."""
    if not labels:
        return []
    seen: list[str] = []
    for raw in labels:
        value = raw.strip()
        if value and value not in seen:
            seen.append(value)
    return seen


# ── Schemas ────────────────────────────────────────────────────────────────


class WazuhCredentialsUpdate(BaseModel):
    """PUT body — the per-org Wazuh credentials + optional scoping fields."""

    indexer_user: str = Field(min_length=1, max_length=200)
    # Omit (or null) to keep the stored password; required on first save.
    indexer_password: str | None = Field(default=None, max_length=1024)
    server_api_user: str = Field(min_length=1, max_length=200)
    server_api_password: str | None = Field(default=None, max_length=1024)
    wazuh_index_filter: str = Field(default=_DEFAULT_INDEX_FILTER, min_length=1, max_length=200)
    # The agent.labels.group value(s) to scope indexer queries to when the
    # filter is enabled (Phase 6.6-f).  Multiple labels are OR-combined.
    agent_group_labels: list[str] | None = None
    inject_group_label_filter: bool = False

    @model_validator(mode="after")
    def _validate_group_labels(self) -> "WazuhCredentialsUpdate":
        cleaned = _clean_labels(self.agent_group_labels)
        self.agent_group_labels = cleaned or None
        if self.inject_group_label_filter and not cleaned:
            raise ValueError(
                "Provide at least one agent group label to restrict indexer "
                "queries, or disable the group-label filter."
            )
        return self


class ProbeResultOut(BaseModel):
    role: str
    ok: bool
    detail: str
    status_code: int | None = None


class WazuhCredentialsResponse(BaseModel):
    configured: bool
    organization_id: str | None = None
    indexer_user: str | None = None
    server_api_user: str | None = None
    wazuh_index_filter: str | None = None
    agent_group_labels: list[str] | None = None
    inject_group_label_filter: bool | None = None
    validated_at: datetime | None = None
    updated_at: datetime | None = None


class WazuhCredentialsSaveResponse(WazuhCredentialsResponse):
    probe_ok: bool = False
    probe_results: list[ProbeResultOut] = Field(default_factory=list)
    agent_count: int | None = None
    group_count: int | None = None
    groups: list[str] | None = None
    scope_detail: str | None = None
    warnings: list[str] = Field(default_factory=list)


class WazuhCredentialHistoryEntry(BaseModel):
    """One credential-change audit row, projected for the rotation log."""

    id: str
    created_at: datetime
    user_id: str | None
    probe_ok: bool | None
    index_filter: str | None
    agent_count: int | None
    group_count: int | None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _source_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _session_id(request: Request) -> str | None:
    session: dict[str, Any] = getattr(request.state, "session", {})
    raw = session.get("session_id")
    return str(raw) if raw is not None else None


async def _load_username(key: str) -> str | None:
    raw = await get_secrets_backend().get(key)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    username = data.get("username") if isinstance(data, dict) else None
    return str(username) if username is not None else None


async def _resolve_credential(
    *, key: str, username: str, password: str | None, label: str
) -> tuple[str, str, str | None]:
    """``(probe_user, probe_password, blob_to_write_or_None)`` — keep-existing.

    A provided password sets/rotates the credential. An omitted password keeps
    the stored blob verbatim (None write) **only when the username is
    unchanged** — a password belongs to a specific user, so switching the
    username with a blank password (e.g. acme → beta) must NOT silently reuse
    the previous user's password; that is a 422. On first configuration an
    omitted password is also a 422.
    """
    if password:
        return username, password, json.dumps({"username": username, "password": password})
    raw = await get_secrets_backend().get(key)
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"{label} password is required when configuring this "
                f"org's credentials for the first time."
            ),
        )
    data = json.loads(raw)
    stored_username = str(data["username"])
    if username != stored_username:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Changing the {label} username requires its password — a "
                f"password belongs to a specific user and cannot be reused for "
                f"a different one."
            ),
        )
    return stored_username, str(data["password"]), None


async def _require_org(db: AsyncSession, organization_id: uuid.UUID) -> Organization:
    org = await db.scalar(select(Organization).where(Organization.id == organization_id))
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return org


def _as_bool(v: object) -> bool | None:
    return v if isinstance(v, bool) else None


def _as_str(v: object) -> str | None:
    return v if isinstance(v, str) else None


def _as_int(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


# ── Routes ─────────────────────────────────────────────────────────────────


@router.get("/{organization_id}/wazuh-credentials", response_model=WazuhCredentialsResponse)
async def get_wazuh_credentials(
    organization_id: uuid.UUID,
    _superuser: Annotated[User, Depends(require_superuser)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WazuhCredentialsResponse:
    await _require_org(db, organization_id)
    row = await db.scalar(
        select(OrganizationWazuhConfig).where(
            OrganizationWazuhConfig.organization_id == organization_id
        )
    )
    if row is None:
        return WazuhCredentialsResponse(configured=False, organization_id=str(organization_id))
    return WazuhCredentialsResponse(
        configured=True,
        organization_id=str(organization_id),
        indexer_user=await _load_username(row.opensearch_credential_key),
        server_api_user=await _load_username(row.server_api_credential_key),
        wazuh_index_filter=row.opensearch_index_pattern,
        agent_group_labels=row.agent_group_labels,
        inject_group_label_filter=row.inject_group_label_filter,
        validated_at=row.validated_at,
        updated_at=row.updated_at,
    )


@router.put("/{organization_id}/wazuh-credentials", response_model=WazuhCredentialsSaveResponse)
async def put_wazuh_credentials(
    organization_id: uuid.UUID,
    payload: WazuhCredentialsUpdate,
    request: Request,
    superuser: Annotated[User, Depends(require_superuser)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WazuhCredentialsSaveResponse:
    await _require_org(db, organization_id)

    # URLs come from the install ecosystem topology (6.6-a) — required.
    topology = await db.scalar(select(WazuhEcosystemTopology))
    if topology is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Configure the install's Wazuh ecosystem topology first "
                "(Settings → Wazuh Ecosystem) — an organization's credentials "
                "query that ecosystem."
            ),
        )
    indexer_url, manager_url, verify_tls = resolve_endpoints_from_topology(topology)

    os_key = opensearch_credential_key(organization_id)
    api_key = server_api_credential_key(organization_id)
    indexer_user, indexer_password, indexer_blob = await _resolve_credential(
        key=os_key, username=payload.indexer_user, password=payload.indexer_password,
        label="Indexer",
    )
    server_user, server_password, server_blob = await _resolve_credential(
        key=api_key, username=payload.server_api_user, password=payload.server_api_password,
        label="Server API",
    )

    # Probe (soft-fail): record the outcome but save regardless.
    probe = await probe_org_credentials(
        indexer_url=indexer_url,
        indexer_user=indexer_user,
        indexer_password=indexer_password,
        index_pattern=payload.wazuh_index_filter,
        server_api_url=manager_url,
        server_api_user=server_user,
        server_api_password=server_password,
        verify_tls=verify_tls,
    )

    # Persist credentials that changed, then the config row.
    secrets = get_secrets_backend()
    if indexer_blob is not None:
        await secrets.set(os_key, indexer_blob)
    if server_blob is not None:
        await secrets.set(api_key, server_blob)

    now = datetime.now(UTC)
    validated_at = now if probe.ok else None
    row = await db.scalar(
        select(OrganizationWazuhConfig).where(
            OrganizationWazuhConfig.organization_id == organization_id
        )
    )
    if row is None:
        row = OrganizationWazuhConfig(
            id=uuid.uuid4(),
            organization_id=organization_id,
            opensearch_url=indexer_url,
            opensearch_index_pattern=payload.wazuh_index_filter,
            opensearch_credential_key=os_key,
            server_api_url=manager_url,
            server_api_credential_key=api_key,
            verify_tls=verify_tls,
            inject_group_label_filter=payload.inject_group_label_filter,
            agent_group_labels=payload.agent_group_labels,
            validated_at=validated_at,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        # Refresh the URL projection from the current topology (a transitional
        # cache until 6.6-e reads the topology fresh per query).
        row.opensearch_url = indexer_url
        row.server_api_url = manager_url
        row.verify_tls = verify_tls
        row.opensearch_index_pattern = payload.wazuh_index_filter
        row.inject_group_label_filter = payload.inject_group_label_filter
        row.agent_group_labels = payload.agent_group_labels
        row.validated_at = validated_at
        row.updated_at = now

    warnings: list[str] = []
    if not probe.ok:
        warnings.append(
            "Credentials saved, but the connection probe failed — verify after "
            "the Wazuh-side user is provisioned. "
            + "; ".join(r.detail for r in (probe.indexer, probe.manager) if not r.ok)
        )

    await write_event(
        db,
        event_type="organization.wazuh_credentials.updated",
        organization_id=organization_id,
        user_id=superuser.id,
        session_id=_session_id(request),
        source_ip=_source_ip(request),
        event_data={
            "index_filter": payload.wazuh_index_filter,
            "agent_group_labels": payload.agent_group_labels,
            "inject_group_label_filter": payload.inject_group_label_filter,
            "probe_ok": probe.ok,
            "indexer_ok": probe.indexer.ok,
            "server_api_ok": probe.manager.ok,
            "agent_count": probe.agent_count,
            "group_count": probe.group_count,
        },
    )
    await db.commit()

    return WazuhCredentialsSaveResponse(
        configured=True,
        organization_id=str(organization_id),
        indexer_user=indexer_user,
        server_api_user=server_user,
        wazuh_index_filter=payload.wazuh_index_filter,
        agent_group_labels=payload.agent_group_labels,
        inject_group_label_filter=payload.inject_group_label_filter,
        validated_at=validated_at,
        updated_at=now,
        probe_ok=probe.ok,
        probe_results=[
            ProbeResultOut(
                role=probe.indexer.role,
                ok=probe.indexer.ok,
                detail=probe.indexer.detail,
                status_code=probe.indexer.status_code,
            ),
            ProbeResultOut(
                role=probe.manager.role,
                ok=probe.manager.ok,
                detail=probe.manager.detail,
                status_code=probe.manager.status_code,
            ),
        ],
        agent_count=probe.agent_count,
        group_count=probe.group_count,
        groups=probe.groups,
        scope_detail=probe.scope_detail,
        warnings=warnings,
    )


@router.get(
    "/{organization_id}/wazuh-credentials/history",
    response_model=list[WazuhCredentialHistoryEntry],
)
async def get_wazuh_credentials_history(
    organization_id: uuid.UUID,
    _superuser: Annotated[User, Depends(require_superuser)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 20,
) -> list[WazuhCredentialHistoryEntry]:
    """This org's Wazuh credential-change audit trail (rotation log).

    Org-scoped projection of the install-wide audit — newest first, capped.
    Never returns credentials (the audit row never stored any).
    """
    await _require_org(db, organization_id)
    rows = (
        await db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.event_type == "organization.wazuh_credentials.updated",
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(max(1, min(limit, 100)))
        )
    ).all()
    entries: list[WazuhCredentialHistoryEntry] = []
    for row in rows:
        data: dict[str, object] = row.event_data if isinstance(row.event_data, dict) else {}
        entries.append(
            WazuhCredentialHistoryEntry(
                id=str(row.id),
                created_at=row.created_at,
                user_id=str(row.user_id) if row.user_id else None,
                probe_ok=_as_bool(data.get("probe_ok")),
                index_filter=_as_str(data.get("index_filter")),
                agent_count=_as_int(data.get("agent_count")),
                group_count=_as_int(data.get("group_count")),
            )
        )
    return entries
