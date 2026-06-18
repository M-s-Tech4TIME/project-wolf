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
    IndexAccessResult,
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
    "agent_group_labels": ["acme"],
    "inject_group_label_filter": False,
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
    groups: int | None = 1,
    group_names: list[str] | None = None,
) -> None:
    names = group_names if group_names is not None else ["acme"]

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
            groups=names if manager_ok else None,
            scope_detail="scope summary",
            index_results=[
                IndexAccessResult(
                    pattern=p, ok=indexer_ok, detail="index probe",
                    status_code=200 if indexer_ok else 401,
                )
                for p in _clean_patterns(kwargs.get("index_patterns"))
            ],
        )

    monkeypatch.setattr(wc, "probe_org_credentials", fake)


def _clean_patterns(patterns: object) -> list[str]:
    return list(patterns) if isinstance(patterns, list) and patterns else ["wazuh-alerts-*"]


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
                {"url": "https://idx1:9200", "name": "indexer-1"},
                {"url": "https://idx2:9200"},
            ],
            "manager_master": {"url": "https://master:55000", "name": "master"},
            "manager_workers": [{"url": "https://worker:55000"}],
            "dashboards": [{"url": "https://dash"}],
        },
        verify_tls=False,
    )
    indexer, manager, verify = resolve_endpoints_from_topology(row)
    assert indexer == "https://idx1:9200"
    assert manager == "https://master:55000"
    assert verify is False


# ── Probe (MockTransport, no network) ────────────────────────────────────────


async def test_probe_org_credentials_ok_with_scope() -> None:
    # Scope groups come from /security/users/me/policies (the credential's true
    # RBAC scope), NOT from the incidental group membership of its agents.
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/_count"):  # indexer read probe
            return httpx.Response(200, json={"count": 27})
        if path.endswith("/security/user/authenticate"):
            return httpx.Response(200, json={"data": {"token": "t"}})
        if path.endswith("/security/users/me/policies"):
            return httpx.Response(
                200,
                json={"data": {"agent:read": {"agent:group:acme": "allow"}}},
            )
        if path.endswith("/agents"):
            return httpx.Response(200, json={"data": {"total_affected_items": 5}})
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        result = await probe_org_credentials(
            indexer_url="https://idx:9200", indexer_user="u", indexer_password="p",
            index_patterns=["wazuh-alerts-*"],
            server_api_url="https://mgr:55000", server_api_user="wui", server_api_password="p",
            verify_tls=False, client=c,
        )
    assert result.ok is True
    assert result.indexer.status_code == 200
    assert "27 doc(s)" in result.indexer.detail
    assert len(result.index_results) == 1 and result.index_results[0].ok is True
    assert result.agent_count == 5
    assert result.group_count == 1
    assert result.groups == ["acme"]
    assert "5 agent" in result.scope_detail
    assert "acme" in result.scope_detail


async def test_probe_org_credentials_scope_ignores_incidental_agent_groups() -> None:
    # Even though the agents belong to extra groups, the scope reflects only the
    # credential's RBAC resources (acme + acme-eu here).
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/_count"):
            return httpx.Response(200, json={"count": 3})
        if path.endswith("/security/user/authenticate"):
            return httpx.Response(200, json={"data": {"token": "t"}})
        if path.endswith("/security/users/me/policies"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "agent:read": {
                            "agent:group:acme": "allow",
                            "agent:group:acme-eu": "allow",
                        }
                    }
                },
            )
        if path.endswith("/agents"):
            return httpx.Response(200, json={"data": {"total_affected_items": 4}})
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        result = await probe_org_credentials(
            indexer_url="https://idx:9200", indexer_user="u", indexer_password="p",
            index_patterns=["wazuh-alerts-*"],
            server_api_url="https://mgr:55000", server_api_user="wui", server_api_password="p",
            verify_tls=False, client=c,
        )
    assert result.groups == ["acme", "acme-eu"]
    assert result.group_count == 2


async def test_probe_org_credentials_indexer_403_is_not_ok() -> None:
    # The 6.6-f fix: a 403 reading the index is a failure, not "authenticated".
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/_count"):
            return httpx.Response(403)
        if request.url.path.endswith("/security/user/authenticate"):
            return httpx.Response(200, json={"data": {"token": "t"}})
        return httpx.Response(200, json={"data": {"total_affected_items": 1}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        result = await probe_org_credentials(
            indexer_url="https://idx:9200", indexer_user="u", indexer_password="p",
            index_patterns=["wazuh-alerts-*"],
            server_api_url="https://mgr:55000", server_api_user="wui", server_api_password="p",
            verify_tls=False, client=c,
        )
    assert result.indexer.ok is False
    assert result.indexer.status_code == 403
    assert "denied read" in result.indexer.detail.lower()
    assert result.ok is False


async def test_probe_org_credentials_manager_auth_fail_no_scope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/_count"):
            return httpx.Response(200, json={"count": 0})
        if request.url.path.endswith("/security/user/authenticate"):
            return httpx.Response(401)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        result = await probe_org_credentials(
            indexer_url="https://idx:9200", indexer_user="u", indexer_password="p",
            index_patterns=["wazuh-alerts-*"],
            server_api_url="https://mgr:55000", server_api_user="wui", server_api_password="bad",
            verify_tls=False, client=c,
        )
    assert result.indexer.ok is True
    assert result.manager.ok is False
    assert result.ok is False
    assert result.agent_count is None
    assert result.groups is None


async def test_probe_org_credentials_scoped_counts_when_inject_on() -> None:
    """Opt-in ON: per-index counts come THROUGH the agent.labels.group filter.

    Mirrors the Q4 definitive validation — a broad credential's raw count is
    huge, but the scoped (filtered) count is what the probe reports.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/_count"):
            # POST (scoped/filtered) → small; GET (raw) → huge.
            scoped = request.method == "POST"
            return httpx.Response(
                200, json={"count": 12 if scoped else 99999, "_shards": {"total": 5}}
            )
        if path.endswith("/security/user/authenticate"):
            return httpx.Response(200, json={"data": {"token": "t"}})
        if path.endswith("/security/users/me/policies"):
            return httpx.Response(200, json={"data": {"agent:read": {"agent:group:*": "allow"}}})
        if path.endswith("/agents"):
            return httpx.Response(200, json={"data": {"total_affected_items": 4}})
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        result = await probe_org_credentials(
            indexer_url="https://idx:9200", indexer_user="u", indexer_password="p",
            index_patterns=["*"],
            server_api_url="https://mgr:55000", server_api_user="wui", server_api_password="p",
            verify_tls=False, client=c,
            inject_group_label_filter=True, agent_group_labels=["acme"],
        )
    assert result.index_results[0].ok is True
    # The scoped (filtered) count, not the raw 99999.
    assert "12 doc(s)" in result.index_results[0].detail
    assert "agent.labels.group" in result.index_results[0].detail


async def test_probe_org_credentials_per_index_mixed_access() -> None:
    """Multiple patterns: one readable, one with 0 shards → per-index breakdown."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/security/user/authenticate"):
            return httpx.Response(200, json={"data": {"token": "t"}})
        if path.endswith("/security/users/me/policies"):
            return httpx.Response(200, json={"data": {"agent:read": {"agent:group:*": "allow"}}})
        if path.endswith("/agents"):
            return httpx.Response(200, json={"data": {"total_affected_items": 9}})
        # indexer _count: real index resolves shards; bogus one resolves none.
        if "wazuh-alerts-" in path:
            return httpx.Response(200, json={"count": 12, "_shards": {"total": 5}})
        return httpx.Response(200, json={"count": 0, "_shards": {"total": 0}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        result = await probe_org_credentials(
            indexer_url="https://idx:9200", indexer_user="u", indexer_password="p",
            index_patterns=["wazuh-alerts-*", "bogus-*"],
            server_api_url="https://mgr:55000", server_api_user="wui", server_api_password="p",
            verify_tls=False, client=c,
        )
    assert len(result.index_results) == 2
    by_pat = {r.pattern: r for r in result.index_results}
    assert by_pat["wazuh-alerts-*"].ok is True
    assert by_pat["bogus-*"].ok is False
    assert "no readable index" in by_pat["bogus-*"].detail.lower()
    # Overall indexer fails because one configured pattern is unreadable.
    assert result.indexer.ok is False
    assert "bogus-*" in result.indexer.detail


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
    # URLs are no longer stored per-org (6.6-g) — only creds + pattern + scope.
    assert row.opensearch_index_pattern == "wazuh-alerts-acme-*"
    assert row.agent_group_labels == ["acme"]
    assert row.inject_group_label_filter is False
    assert row.validated_at is not None


async def test_put_multiple_index_patterns_normalized_and_checked(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    with_topology: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Comma-separated patterns are trimmed/de-duped + each gets an access result."""
    _stub_probe(monkeypatch)
    org_id = await _make_org(db)
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200

    payload = {
        **_BODY,
        "wazuh_index_filter": " wazuh-alerts-* , wazuh-archives-* , wazuh-alerts-* ",
    }
    resp = await client.put(
        f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials", json=payload
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Stored value is normalized (trimmed, de-duped, comma-joined).
    row = await db.scalar(
        select(OrganizationWazuhConfig).where(OrganizationWazuhConfig.organization_id == org_id)
    )
    assert row is not None
    assert row.opensearch_index_pattern == "wazuh-alerts-*,wazuh-archives-*"
    # One per-index access result per cleaned pattern.
    assert [r["pattern"] for r in body["index_results"]] == ["wazuh-alerts-*", "wazuh-archives-*"]


async def test_put_blank_index_pattern_is_422(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    with_topology: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_probe(monkeypatch)
    org_id = await _make_org(db)
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200
    payload = {**_BODY, "wazuh_index_filter": "  ,  "}
    resp = await client.put(
        f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials", json=payload
    )
    assert resp.status_code == 422
    assert "at least one index pattern" in resp.text.lower()


async def test_put_inject_on_without_labels_is_422(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    with_topology: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabling the group-label filter with no labels is rejected (can't inject empty)."""
    _stub_probe(monkeypatch)
    org_id = await _make_org(db)
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200

    payload = {**_BODY, "inject_group_label_filter": True, "agent_group_labels": []}
    resp = await client.put(
        f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials", json=payload
    )
    assert resp.status_code == 422
    assert "at least one agent group label" in resp.text.lower()


async def test_put_inject_on_with_labels_persists(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    with_topology: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_probe(monkeypatch)
    org_id = await _make_org(db)
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200

    payload = {**_BODY, "inject_group_label_filter": True, "agent_group_labels": ["acme", "acme"]}
    resp = await client.put(
        f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials", json=payload
    )
    assert resp.status_code == 200, resp.text
    row = await db.scalar(
        select(OrganizationWazuhConfig).where(OrganizationWazuhConfig.organization_id == org_id)
    )
    assert row is not None
    assert row.inject_group_label_filter is True
    assert row.agent_group_labels == ["acme"]  # de-duped


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


async def test_put_username_change_without_password_is_422(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    with_topology: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Switching a username with a blank password must NOT reuse the old creds."""
    _stub_probe(monkeypatch)
    org_id = await _make_org(db)
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200

    # First save (wolf_ro / wazuh-wui with passwords).
    assert (
        await client.put(
            f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials", json=_BODY
        )
    ).status_code == 200

    # Change ONLY the indexer username, omit passwords → 422 (can't reuse acme's pw).
    changed = {**_BODY, "indexer_user": "wolf-beta",
               "indexer_password": None, "server_api_password": None}
    resp = await client.put(
        f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials", json=changed
    )
    assert resp.status_code == 422
    assert "username requires its password" in resp.text.lower()

    # The stored credential is unchanged (still the original user).
    raw = await get_secrets_backend().get(opensearch_credential_key(org_id))
    assert raw is not None and json.loads(raw)["username"] == "wolf_ro"


async def test_put_username_change_with_password_succeeds(
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

    # Username change WITH a password is accepted and stored.
    changed = {**_BODY, "indexer_user": "wolf-beta", "indexer_password": "beta-idx-secret"}
    resp = await client.put(
        f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials", json=changed
    )
    assert resp.status_code == 200, resp.text
    raw = await get_secrets_backend().get(opensearch_credential_key(org_id))
    assert raw is not None
    blob = json.loads(raw)
    assert blob["username"] == "wolf-beta"
    assert blob["password"] == "beta-idx-secret"


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


# ── Rotation log (history) ───────────────────────────────────────────────────


async def test_history_returns_credential_changes(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    with_topology: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_probe(monkeypatch, agents=9, groups=5)
    org_id = await _make_org(db)
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200
    assert (
        await client.put(
            f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials", json=_BODY
        )
    ).status_code == 200

    resp = await client.get(
        f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials/history"
    )
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    assert entries[0]["probe_ok"] is True
    assert entries[0]["index_filter"] == "wazuh-alerts-acme-*"
    assert entries[0]["agent_count"] == 9


async def test_history_requires_superuser(
    client: AsyncClient, seed_organization_and_user: dict[str, Any]
) -> None:
    login = await _login(client, seed_organization_and_user["user_email"], "password123")
    assert login.status_code == 200
    org_id = seed_organization_and_user["organization_id"]
    resp = await client.get(
        f"/api/v1/superuser/organizations/{org_id}/wazuh-credentials/history"
    )
    assert resp.status_code == 403
