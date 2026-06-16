"""Install-level Wazuh ecosystem topology API — Phase 6.6-a, ADR 0020.

GET  /api/v1/superuser/wazuh-topology
    Return the configured install-wide Wazuh ecosystem topology (or
    ``configured: false`` when none is set).  Usernames are surfaced;
    passwords never are.

PUT  /api/v1/superuser/wazuh-topology
    Configure / re-configure the topology.  **Validate-before-persist with
    HARD fail** (ADR 0020 decision 3): every required endpoint is probed and
    the save is REJECTED if any fails — Wolf must not commit to an
    unreachable ecosystem.  For a distributed deployment, worker-node probe
    failures are *warnings*, not blockers (a worker can be temporarily down);
    indexer nodes, the manager master and the dashboard are blockers.

Both routes are **Superuser-only** (ADR 0018 + ADR 0020: ALL Wazuh
configuration is concentrated in the Superuser).  Credentials live in the
secrets backend (ADR 0020 decision 7); the DB row holds only their keys.
A password omitted on PUT means "keep the existing credential" so the
Superuser can edit URLs without re-typing secrets.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wolf_server.api.superuser import require_superuser
from wolf_server.audit.log import write_event
from wolf_server.database import get_db
from wolf_server.organization.models import User
from wolf_server.secrets_factory import get_secrets_backend
from wolf_server.wazuh.models import WazuhEcosystemTopology
from wolf_server.wazuh.probe import (
    EndpointProbeResult,
    probe_dashboard,
    probe_indexer,
    probe_manager_api,
)
from wolf_server.wazuh.topology import (
    DistributedTopology,
    SingleHostTopology,
    WazuhTopology,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/superuser", tags=["superuser-wazuh"])

# Install-level secrets-backend keys (no org id — one install = one ecosystem).
INDEXER_CREDENTIAL_KEY = "wazuh.topology.indexer_admin"
MANAGER_CREDENTIAL_KEY = "wazuh.topology.manager_api"

_TOPOLOGY_ADAPTER: TypeAdapter[Any] = TypeAdapter(WazuhTopology)


# ── Schemas ────────────────────────────────────────────────────────────────


class WazuhTopologyUpdate(BaseModel):
    """PUT body — the structural shape plus write-only credentials."""

    topology: WazuhTopology
    indexer_admin_user: str = Field(min_length=1, max_length=200)
    # Omit (or send null) to keep the stored password; required on first save.
    indexer_admin_password: str | None = Field(default=None, max_length=1024)
    manager_api_user: str = Field(min_length=1, max_length=200)
    manager_api_password: str | None = Field(default=None, max_length=1024)
    verify_tls: bool = True


class ProbeResultOut(BaseModel):
    role: str
    url: str
    ok: bool
    detail: str
    status_code: int | None = None

    @classmethod
    def of(cls, r: EndpointProbeResult) -> "ProbeResultOut":
        return cls(role=r.role, url=r.url, ok=r.ok, detail=r.detail, status_code=r.status_code)


class WazuhTopologyResponse(BaseModel):
    configured: bool
    kind: str | None = None
    topology: WazuhTopology | None = None
    indexer_admin_user: str | None = None
    manager_api_user: str | None = None
    verify_tls: bool | None = None
    validated_at: datetime | None = None
    updated_at: datetime | None = None


class WazuhTopologySaveResponse(WazuhTopologyResponse):
    probe_results: list[ProbeResultOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _source_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _session_id(request: Request) -> str | None:
    session: dict[str, Any] = getattr(request.state, "session", {})
    raw = session.get("session_id")
    return str(raw) if raw is not None else None


async def _load_username(key: str) -> str | None:
    """Return the username stored in a credential blob (never the password)."""
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
    """Resolve the credential to probe with + the blob to persist (if changed).

    Returns ``(probe_user, probe_password, blob_to_write_or_None)``.  When the
    password is omitted the existing blob is kept verbatim (``None`` write) —
    so editing URLs never forces re-typing secrets; on first configuration an
    omitted password is a 422.
    """
    if password:
        blob = json.dumps({"username": username, "password": password})
        return username, password, blob
    raw = await get_secrets_backend().get(key)
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"{label} password is required when configuring the Wazuh "
                f"ecosystem for the first time."
            ),
        )
    data = json.loads(raw)
    return str(data["username"]), str(data["password"]), None


async def _probe_topology(
    topology: WazuhTopology,
    *,
    indexer_user: str,
    indexer_password: str,
    manager_user: str,
    manager_password: str,
    verify_tls: bool,
) -> tuple[list[EndpointProbeResult], list[EndpointProbeResult]]:
    """Probe every endpoint, returning ``(blocking_results, warning_results)``.

    Blocking = indexer node(s), manager master, dashboard.  Warnings =
    distributed manager worker nodes (ADR 0020: a worker may be temporarily
    down without blocking the save).
    """
    blocking: list[EndpointProbeResult] = []
    warnings: list[EndpointProbeResult] = []
    async with httpx.AsyncClient(
        verify=verify_tls, timeout=httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=10.0)
    ) as client:
        if isinstance(topology, SingleHostTopology):
            blocking.append(
                await probe_indexer(
                    topology.indexer_url, indexer_user, indexer_password,
                    verify_tls=verify_tls, client=client,
                )
            )
            blocking.append(
                await probe_manager_api(
                    topology.manager_url, manager_user, manager_password,
                    verify_tls=verify_tls, client=client,
                )
            )
            blocking.append(
                await probe_dashboard(topology.dashboard_url, verify_tls=verify_tls, client=client)
            )
        elif isinstance(topology, DistributedTopology):
            for node in topology.indexer_nodes:
                blocking.append(
                    await probe_indexer(
                        node.url, indexer_user, indexer_password,
                        verify_tls=verify_tls, client=client,
                    )
                )
            blocking.append(
                await probe_manager_api(
                    topology.manager_master_url, manager_user, manager_password,
                    verify_tls=verify_tls, client=client,
                )
            )
            blocking.append(
                await probe_dashboard(topology.dashboard_url, verify_tls=verify_tls, client=client)
            )
            for worker_url in topology.manager_worker_urls:
                warnings.append(
                    await probe_manager_api(
                        worker_url, manager_user, manager_password,
                        verify_tls=verify_tls, client=client,
                    )
                )
    return blocking, warnings


# ── Routes ─────────────────────────────────────────────────────────────────


@router.get("/wazuh-topology", response_model=WazuhTopologyResponse)
async def get_wazuh_topology(
    _superuser: Annotated[User, Depends(require_superuser)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WazuhTopologyResponse:
    row = await db.scalar(select(WazuhEcosystemTopology))
    if row is None:
        return WazuhTopologyResponse(configured=False)
    return WazuhTopologyResponse(
        configured=True,
        kind=row.kind,
        topology=_TOPOLOGY_ADAPTER.validate_python(row.topology),
        indexer_admin_user=await _load_username(row.indexer_credential_key),
        manager_api_user=await _load_username(row.manager_credential_key),
        verify_tls=row.verify_tls,
        validated_at=row.validated_at,
        updated_at=row.updated_at,
    )


@router.put("/wazuh-topology", response_model=WazuhTopologySaveResponse)
async def put_wazuh_topology(
    payload: WazuhTopologyUpdate,
    request: Request,
    superuser: Annotated[User, Depends(require_superuser)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WazuhTopologySaveResponse:
    # 1. Resolve credentials (keep-existing semantics; 422 if first-save w/o pw).
    indexer_user, indexer_password, indexer_blob = await _resolve_credential(
        key=INDEXER_CREDENTIAL_KEY,
        username=payload.indexer_admin_user,
        password=payload.indexer_admin_password,
        label="Indexer admin",
    )
    manager_user, manager_password, manager_blob = await _resolve_credential(
        key=MANAGER_CREDENTIAL_KEY,
        username=payload.manager_api_user,
        password=payload.manager_api_password,
        label="Manager API",
    )

    # 2. Probe BEFORE persisting anything (validate-before-persist).
    blocking, warning_probes = await _probe_topology(
        payload.topology,
        indexer_user=indexer_user,
        indexer_password=indexer_password,
        manager_user=manager_user,
        manager_password=manager_password,
        verify_tls=payload.verify_tls,
    )
    all_probes = [*blocking, *warning_probes]
    failed_blocking = [r for r in blocking if not r.ok]

    if failed_blocking:
        # Audit the rejected attempt (security-relevant; never logs creds).
        await write_event(
            db,
            event_type="install.wazuh_topology.probe_failed",
            organization_id=None,
            user_id=superuser.id,
            session_id=_session_id(request),
            source_ip=_source_ip(request),
            event_data={
                "kind": payload.topology.kind,
                "failed_endpoints": [
                    {"role": r.role, "url": r.url, "detail": r.detail} for r in failed_blocking
                ],
            },
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Wazuh ecosystem not saved — one or more required endpoints "
                "failed the connection probe: "
                + "; ".join(r.detail for r in failed_blocking)
            ),
        )

    # 3. Persist credentials that changed, then the topology row + audit.
    secrets = get_secrets_backend()
    if indexer_blob is not None:
        await secrets.set(INDEXER_CREDENTIAL_KEY, indexer_blob)
    if manager_blob is not None:
        await secrets.set(MANAGER_CREDENTIAL_KEY, manager_blob)

    now = datetime.now(UTC)
    topology_doc = payload.topology.model_dump(mode="json")
    row = await db.scalar(select(WazuhEcosystemTopology))
    if row is None:
        row = WazuhEcosystemTopology(
            id=uuid.uuid4(),
            is_singleton=True,
            kind=payload.topology.kind,
            topology=topology_doc,
            indexer_credential_key=INDEXER_CREDENTIAL_KEY,
            manager_credential_key=MANAGER_CREDENTIAL_KEY,
            verify_tls=payload.verify_tls,
            validated_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.kind = payload.topology.kind
        row.topology = topology_doc
        row.verify_tls = payload.verify_tls
        row.validated_at = now
        row.updated_at = now

    warnings = [r.detail for r in warning_probes if not r.ok]
    await write_event(
        db,
        event_type="install.wazuh_topology.updated",
        organization_id=None,
        user_id=superuser.id,
        session_id=_session_id(request),
        source_ip=_source_ip(request),
        event_data={
            "kind": payload.topology.kind,
            "endpoints_probed": [{"role": r.role, "url": r.url, "ok": r.ok} for r in all_probes],
            "worker_warnings": warnings,
        },
    )
    await db.commit()

    return WazuhTopologySaveResponse(
        configured=True,
        kind=payload.topology.kind,
        topology=payload.topology,
        indexer_admin_user=indexer_user,
        manager_api_user=manager_user,
        verify_tls=payload.verify_tls,
        validated_at=now,
        updated_at=now,
        probe_results=[ProbeResultOut.of(r) for r in all_probes],
        warnings=warnings,
    )
