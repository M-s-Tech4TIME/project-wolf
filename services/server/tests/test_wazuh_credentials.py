"""Tests for Phase 6.6-c — per-org Wazuh credentials backend.

GET/PUT /api/v1/superuser/organizations/{id}/wazuh-credentials (Superuser-only,
ADR 0020).  The save is **soft-fail**: credentials persist even when the probe
fails (validated_at stays null), so the Superuser can save before the Wazuh-
side user is provisioned.  URLs come from the install ecosystem topology
(6.6-a) — a PUT without a topology is a 409.

The per-org probe is stubbed at the API boundary for the endpoint tests (no
network); the probe's own wire-behaviour + scope summary are covered directly
via httpx.MockTransport.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_server.api import wazuh_credentials as wc
from wolf_server.audit.models import AuditEvent
from wolf_server.auth.local import hash_password
from wolf_server.bootstrap.superuser import SUPERUSER_EMAIL, SUPERUSER_USERNAME
from wolf_server.organization.models import Organization, User
from wolf_server.secrets_factory import get_secrets_backend
from wolf_server.wazuh.credentials import (
    OrgCredentialProbeResult,
    probe_org_credentials,
    resolve_endpoints_from_topology,
)
from wolf_server.wazuh.models import OrganizationWazuhConfig, WazuhEcosystemTopology
from wolf_server.wazuh.probe import EndpointProbeResult
from wolf_server.wazuh.resolver import opensearch_credential_key, server_api_credential_key

_WOLF_PASSWORD = "test-wolf-password-32-chars-long!!"

_BODY = {
    "indexer_user": "wolf_ro",
    "indexer_password": "idx-secret",
    "server_api_user": "wazuh-wui",
    "server_api_password": "api-secret",
    "wazuh_index_filter": "wazuh-alerts-acme-*",
    "wazuh_agent_groups": ["default", "acme"],
    "inject_organization_filter": False,
}


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture(autouse=True)
async def _clean(db: AsyncSession) -> None:
    """Clean slate: the singleton topology row (shared across the suite)."""
    await db.execute(delete(WazuhEcosystemTopology))
    await db.commit()


@pytest_asyncio.fixture
async def with_topology(db: AsyncSession, _clean: None) -> None:
    """Insert a single-host install topology so PUTs have URLs to resolve."""
    now = datetime.now(UTC)
    db.add(
        WazuhEcosystemTopology(
            id=uuid.uuid4(),
            is_singleton=True,
            kind="single",
            topology={
                "kind": "single",
                "indexer_url": "https://wz:9200",
                "manager_url": "https://wz:55000",
                "dashboard_url": "https://wz",
            },
            indexer_credential_key="wazuh.topology.indexer_admin",
            manager_credential_key="wazuh.topology.manager_api",
            verify_tls=False,
            validated_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    await db.commit()


@pytest_asyncio.fixture
async def seed_superuser(db: AsyncSession) -> dict[str, Any]:
    existing = await db.scalar(select(User).where(User.email == SUPERUSER_EMAIL))
    if existing is None:
        existing = User(
            id=uuid.uuid4(),
            email=SUPERUSER_EMAIL,
            display_name=SUPERUSER_USERNAME,
            hashed_password=hash_password(_WOLF_PASSWORD),
            is_active=True,
            is_superuser=True,
            verification_status="verified",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(existing)
        await db.commit()
    return {"user_id": existing.id}


async def _make_org(db: AsyncSession) -> uuid.UUID:
    suffix = uuid.uuid4().hex[:8]
    org = Organization(
        id=uuid.uuid4(),
        name=f"Cred Org {suffix}",
        slug=f"cred-org-{suffix}",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(org)
    await db.commit()
    return org.id


def _stub_probe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    indexer_ok: bool = True,
    manager_ok: bool = True,
    agents: int | None = 3,
    groups: int | None = 2,
) -> None:
    async def fake(**kwargs: Any) -> OrgCredentialProbeResult:
        ind = EndpointProbeResult(
            role="indexer", url=kwargs["indexer_url"], ok=indexer_ok,
            detail="indexer probe", status_code=200 if indexer_ok else 401,
        )
        mgr = EndpointProbeResult(
            role="manager", url=kwargs["server_api_url"], ok=manager_ok,
            detail="manager probe", status_code=200 if manager_ok else 401,
        )
        return OrgCredentialProbeResult(
            indexer=ind,
            manager=mgr,
            agent_count=agents if manager_ok else None,
            group_count=groups if manager_ok else None,
            scope_detail="scope summary",
        )

    monkeypatch.setattr(wc, "probe_org_credentials", fake)


async def _login(client: AsyncClient, email: str, password: str) -> Any:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


# ── Pure helpers ─────────────────────────────────────────────────────────────


def test_resolve_endpoints_single() -> None:
    row = WazuhEcosystemTopology(
        kind="single",
        topology={
            "kind": "single",
            "indexer_url": "https://idx:9200",
            "manager_url": "https://mgr:55000",
            "dashboard_url": "https://dash",
        },
        verify_tls=True,
    )
    indexer, manager, verify = resolve_endpoints_from_topology(row)
    assert indexer == "https://idx:9200"
    assert manager == "https://mgr:55000"
    assert verify is True


def test_resolve_endpoints_distributed_picks_first_node_and_master() -> None:
    row = WazuhEcosystemTopology(
        kind="distributed",
        topology={
            "kind": "distributed",
            "indexer_nodes": [
                {"url": "https://idx1:9200", "cluster_name": "wazuh"},
                {"url": "https://idx2:9200", "cluster_name": "wazuh"},
            ],
            "manager_master_url": "https://master:55000",
            "manager_worker_urls": ["https://worker:55000"],
            "dashboard_url": "https://dash",
        },
        verify_tls=False,
    )
    indexer, manager, verify = resolve_endpoints_from_topology(row)
    assert indexer == "https://idx1:9200"
    assert manager == "https://master:55000"
    assert verify is False


# ── Probe (MockTransport, no network) ────────────────────────────────────────


async def test_probe_org_credentials_ok_with_scope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/security/user/authenticate"):
            return httpx.Response(200, json={"data": {"token": "t"}})
        if path.endswith("/agents"):
            return httpx.Response(200, json={"data": {"total_affected_items": 5}})
        if path.endswith("/groups"):
            return httpx.Response(200, json={"data": {"total_affected_items": 3}})
        return httpx.Response(200, json={})  # indexer GET "/"

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        result = await probe_org_credentials(
            indexer_url="https://idx:9200", indexer_user="u", indexer_password="p",
            server_api_url="https://mgr:55000", server_api_user="wui", server_api_password="p",
            verify_tls=False, client=c,
        )
    assert result.ok is True
    assert result.agent_count == 5
    assert result.group_count == 3
    assert "5 agent" in result.scope_detail


async def test_probe_org_credentials_manager_auth_fail_no_scope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/security/user/authenticate"):
            return httpx.Response(401)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        result = await probe_org_credentials(
            indexer_url="https://idx:9200", indexer_user="u", indexer_password="p",
            server_api_url="https://mgr:55000", server_api_user="wui", server_api_password="bad",
            verify_tls=False, client=c,
        )
    assert result.indexer.ok is True
    assert result.manager.ok is False
    assert result.ok is False
    assert result.agent_count is None


# ── Authorization ────────────────────────────────────────────────────────────


async def test_unauthenticated_rejected(client: AsyncClient) -> None:
    resp = await client.get(f"/api/v1/superuser/organizations/{uuid.uuid4()}/wazuh-credentials")
    assert resp.status_code == 401


async def test_requires_superuser(
    client: AsyncClient, seed_organization_and_user: dict[str, Any]
) -> None:
    login = await _login(client, seed_organization_and_user["user_email"], "password123")
    assert login.status_code == 200
    org_id = seed_organization_and_user["organization_id"]
    resp = await client.get(f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials")
    assert resp.status_code == 403


# ── GET ──────────────────────────────────────────────────────────────────────


async def test_get_unknown_org_404(client: AsyncClient, seed_superuser: dict[str, Any]) -> None:
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200
    resp = await client.get(f"/api/v1/superuser/organizations/{uuid.uuid4()}/wazuh-credentials")
    assert resp.status_code == 404


async def test_get_not_configured(
    client: AsyncClient, db: AsyncSession, seed_superuser: dict[str, Any]
) -> None:
    org_id = await _make_org(db)
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200
    body = (await client.get(f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials")).json()
    assert body["configured"] is False
    assert body["organization_id"] == str(org_id)


# ── PUT ──────────────────────────────────────────────────────────────────────


async def test_put_requires_topology_first(
    client: AsyncClient, db: AsyncSession, seed_superuser: dict[str, Any]
) -> None:
    org_id = await _make_org(db)
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200
    # No topology configured (autouse clean removed it) → 409.
    resp = await client.put(
        f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials", json=_BODY
    )
    assert resp.status_code == 409
    assert "ecosystem topology first" in resp.text.lower()


async def test_put_happy_path_probe_ok(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    with_topology: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_probe(monkeypatch, agents=7, groups=4)
    org_id = await _make_org(db)
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200

    resp = await client.put(
        f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials", json=_BODY
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is True
    assert body["probe_ok"] is True
    assert body["validated_at"] is not None
    assert body["agent_count"] == 7
    assert body["group_count"] == 4
    assert body["warnings"] == []
    assert "password" not in json.dumps(body)

    # Secrets carry the passwords (under the per-org keys the resolver reads).
    raw = await get_secrets_backend().get(opensearch_credential_key(org_id))
    assert raw is not None and json.loads(raw)["password"] == "idx-secret"
    raw2 = await get_secrets_backend().get(server_api_credential_key(org_id))
    assert raw2 is not None and json.loads(raw2)["password"] == "api-secret"

    # Row populated; URLs sourced from the topology; agent groups stored.
    row = await db.scalar(
        select(OrganizationWazuhConfig).where(
            OrganizationWazuhConfig.organization_id == org_id
        )
    )
    assert row is not None
    assert row.opensearch_url == "https://wz:9200"
    assert row.server_api_url == "https://wz:55000"
    assert row.opensearch_index_pattern == "wazuh-alerts-acme-*"
    assert row.wazuh_agent_groups == ["default", "acme"]
    assert row.validated_at is not None


async def test_put_soft_fail_saves_with_warning(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    with_topology: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Probe fails (Wazuh-side user not provisioned yet) — must still save.
    _stub_probe(monkeypatch, manager_ok=False)
    org_id = await _make_org(db)
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200

    resp = await client.put(
        f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials", json=_BODY
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is True
    assert body["probe_ok"] is False
    assert body["validated_at"] is None
    assert len(body["warnings"]) == 1

    # Persisted despite the failed probe; validated_at stays null.
    row = await db.scalar(
        select(OrganizationWazuhConfig).where(
            OrganizationWazuhConfig.organization_id == org_id
        )
    )
    assert row is not None
    assert row.validated_at is None
    assert await get_secrets_backend().get(opensearch_credential_key(org_id)) is not None


async def test_put_first_save_requires_password(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    with_topology: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_probe(monkeypatch)
    org_id = await _make_org(db)
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200

    payload = {**_BODY, "indexer_password": None}
    resp = await client.put(
        f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials", json=payload
    )
    assert resp.status_code == 422
    assert "password is required" in resp.text.lower()


async def test_put_keeps_existing_password_when_omitted(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    with_topology: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_probe(monkeypatch)
    org_id = await _make_org(db)
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200

    # First save with passwords.
    assert (
        await client.put(
            f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials", json=_BODY
        )
    ).status_code == 200

    # Re-save changing only the index filter, omitting passwords → kept.
    edited = {**_BODY, "indexer_password": None, "server_api_password": None,
              "wazuh_index_filter": "wazuh-alerts-acme-2-*"}
    resp = await client.put(
        f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials", json=edited
    )
    assert resp.status_code == 200, resp.text
    raw = await get_secrets_backend().get(opensearch_credential_key(org_id))
    assert raw is not None and json.loads(raw)["password"] == "idx-secret"


async def test_put_audit_carries_no_credentials(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    with_topology: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_probe(monkeypatch)
    org_id = await _make_org(db)
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200
    assert (
        await client.put(
            f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials", json=_BODY
        )
    ).status_code == 200

    event = await db.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == "organization.wazuh_credentials.updated",
            AuditEvent.organization_id == org_id,
        )
    )
    assert event is not None
    blob = json.dumps(event.event_data)
    assert "idx-secret" not in blob
    assert "api-secret" not in blob
    assert "password" not in blob
