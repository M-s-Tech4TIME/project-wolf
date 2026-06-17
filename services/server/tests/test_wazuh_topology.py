"""Tests for Phase 6.6-a — install-level Wazuh ecosystem topology.

GET/PUT /api/v1/superuser/wazuh-topology (Superuser-only, ADR 0020).  The
endpoint validates-before-persist with a HARD fail (any required endpoint
probe failure rejects the save); distributed worker-node failures are
warnings, not blockers.  Credentials live in the secrets backend — the API
never returns a password and never writes one to the audit log.

The Wazuh probes are stubbed at the API module boundary so the suite runs
with no network (no skips); the probe wire-behaviour itself is covered by
test_wazuh_probe.py.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_server.api import wazuh_topology as topo
from wolf_server.audit.models import AuditEvent
from wolf_server.auth.local import hash_password
from wolf_server.bootstrap.superuser import SUPERUSER_EMAIL, SUPERUSER_USERNAME
from wolf_server.organization.models import User
from wolf_server.secrets_factory import get_secrets_backend
from wolf_server.wazuh.models import WazuhEcosystemTopology
from wolf_server.wazuh.probe import EndpointProbeResult
from wolf_server.wazuh.topology import DistributedTopology, SingleHostTopology, WazuhNode

_WOLF_PASSWORD = "test-wolf-password-32-chars-long!!"

_SINGLE = {
    "topology": {
        "kind": "single",
        "indexer_url": "https://wazuh.example:9200",
        "manager_url": "https://wazuh.example:55000",
        "dashboard_url": "https://wazuh.example:443",
    },
    "indexer_admin_user": "admin",
    "indexer_admin_password": "idx-secret",
    "manager_api_user": "wazuh-wui",
    "manager_api_password": "mgr-secret",
    "verify_tls": False,
}

_DISTRIBUTED = {
    "topology": {
        "kind": "distributed",
        "indexer_nodes": [
            {"url": "https://idx1:9200", "name": "indexer-1"},
            {"url": "https://idx2:9200"},
        ],
        "manager_master": {"url": "https://master:55000", "name": "master"},
        "manager_workers": [
            {"url": "https://worker1:55000"},
            {"url": "https://worker2:55000", "name": "worker-2"},
        ],
        "dashboards": [
            {"url": "https://dash1:443", "name": "primary"},
            {"url": "https://dash2:443"},
        ],
    },
    "indexer_admin_user": "admin",
    "indexer_admin_password": "idx-secret",
    "manager_api_user": "wazuh-wui",
    "manager_api_password": "mgr-secret",
    "verify_tls": False,
}


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture(autouse=True)
async def _clean_topology(db: AsyncSession) -> None:
    """Give every test a clean slate (singleton table + install secrets).

    The API commits to the shared session-scoped engine, so a row/secret left
    by one test would otherwise leak into the next.
    """
    await db.execute(delete(WazuhEcosystemTopology))
    await db.commit()
    secrets = get_secrets_backend()
    await secrets.delete(topo.INDEXER_CREDENTIAL_KEY)
    await secrets.delete(topo.MANAGER_CREDENTIAL_KEY)


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


def _install_probe_stubs(
    monkeypatch: pytest.MonkeyPatch, fail_urls: frozenset[str] = frozenset()
) -> None:
    """Patch the three probes so any URL in ``fail_urls`` fails, else passes."""

    def _result(role: str, url: str) -> EndpointProbeResult:
        ok = url not in fail_urls
        return EndpointProbeResult(
            role=role,  # type: ignore[arg-type]
            url=url,
            ok=ok,
            detail=f"{role} {url} {'ok' if ok else 'FAILED'}",
            status_code=200 if ok else 401,
        )

    async def fake_indexer(
        url: str, u: str, p: str, *, verify_tls: bool, client: Any = None
    ) -> EndpointProbeResult:
        return _result("indexer", url)

    async def fake_manager(
        url: str, u: str, p: str, *, verify_tls: bool, client: Any = None
    ) -> EndpointProbeResult:
        return _result("manager", url)

    async def fake_dashboard(
        url: str, *, verify_tls: bool, client: Any = None
    ) -> EndpointProbeResult:
        return _result("dashboard", url)

    monkeypatch.setattr(topo, "probe_indexer", fake_indexer)
    monkeypatch.setattr(topo, "probe_manager_api", fake_manager)
    monkeypatch.setattr(topo, "probe_dashboard", fake_dashboard)


async def _login(client: AsyncClient, email: str, password: str) -> Any:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


# ── Model validation ─────────────────────────────────────────────────────────


def test_single_topology_requires_http_scheme() -> None:
    with pytest.raises(ValidationError):
        SingleHostTopology(
            indexer_url="ftp://x:9200",
            manager_url="https://x:55000",
            dashboard_url="https://x",
        )


def test_distributed_requires_at_least_one_indexer_node() -> None:
    with pytest.raises(ValidationError):
        DistributedTopology(
            indexer_nodes=[],
            manager_master=WazuhNode(url="https://master:55000"),
            dashboards=[WazuhNode(url="https://dash")],
        )


def test_distributed_requires_at_least_one_dashboard() -> None:
    with pytest.raises(ValidationError):
        DistributedTopology(
            indexer_nodes=[WazuhNode(url="https://idx:9200")],
            manager_master=WazuhNode(url="https://master:55000"),
            dashboards=[],
        )


def test_distributed_validates_worker_urls() -> None:
    with pytest.raises(ValidationError):
        DistributedTopology(
            indexer_nodes=[WazuhNode(url="https://idx:9200")],
            manager_master=WazuhNode(url="https://master:55000"),
            manager_workers=[WazuhNode(url="not-a-url")],
            dashboards=[WazuhNode(url="https://dash")],
        )


def test_distributed_node_name_is_optional_and_blank_coerces_to_none() -> None:
    t = DistributedTopology(
        indexer_nodes=[WazuhNode(url="https://idx:9200")],
        manager_master=WazuhNode(url="https://master:55000", name="  "),
        manager_workers=[],
        dashboards=[WazuhNode(url="https://dash", name="primary")],
    )
    assert t.indexer_nodes[0].name is None  # omitted
    assert t.manager_master.name is None  # blank → None
    assert t.dashboards[0].name == "primary"


# ── Authorization ────────────────────────────────────────────────────────────


async def test_get_unauthenticated_rejected(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/superuser/wazuh-topology")
    assert resp.status_code == 401


async def test_get_requires_superuser(
    client: AsyncClient, seed_organization_and_user: dict[str, Any]
) -> None:
    login = await _login(client, seed_organization_and_user["user_email"], "password123")
    assert login.status_code == 200
    resp = await client.get("/api/v1/superuser/wazuh-topology")
    assert resp.status_code == 403


async def test_put_requires_superuser(
    client: AsyncClient, seed_organization_and_user: dict[str, Any]
) -> None:
    login = await _login(client, seed_organization_and_user["user_email"], "password123")
    assert login.status_code == 200
    resp = await client.put("/api/v1/superuser/wazuh-topology", json=_SINGLE)
    assert resp.status_code == 403


# ── GET when unconfigured ────────────────────────────────────────────────────


async def test_get_not_configured(client: AsyncClient, seed_superuser: dict[str, Any]) -> None:
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200
    body = (await client.get("/api/v1/superuser/wazuh-topology")).json()
    assert body["configured"] is False
    assert body["topology"] is None


# ── PUT happy paths ──────────────────────────────────────────────────────────


async def test_put_single_host_happy_path(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_probe_stubs(monkeypatch)
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200

    resp = await client.put("/api/v1/superuser/wazuh-topology", json=_SINGLE)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is True
    assert body["kind"] == "single"
    assert body["validated_at"] is not None
    assert body["warnings"] == []
    # 3 endpoints probed (indexer + manager + dashboard).
    assert len(body["probe_results"]) == 3
    assert all(p["ok"] for p in body["probe_results"])

    # GET reflects it; username surfaced, password never.
    got = (await client.get("/api/v1/superuser/wazuh-topology")).json()
    assert got["configured"] is True
    assert got["indexer_admin_user"] == "admin"
    assert got["manager_api_user"] == "wazuh-wui"
    assert "password" not in json.dumps(got)

    # Secret blob carries the password; the row does not.
    raw = await get_secrets_backend().get(topo.INDEXER_CREDENTIAL_KEY)
    assert raw is not None and json.loads(raw)["password"] == "idx-secret"
    row = await db.scalar(select(WazuhEcosystemTopology))
    assert row is not None
    assert "idx-secret" not in json.dumps(row.topology)


async def test_put_audit_carries_no_credentials(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_probe_stubs(monkeypatch)
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200
    assert (await client.put("/api/v1/superuser/wazuh-topology", json=_SINGLE)).status_code == 200

    event = await db.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "install.wazuh_topology.updated")
    )
    assert event is not None
    assert event.organization_id is None  # system-level row
    blob = json.dumps(event.event_data)
    assert "idx-secret" not in blob
    assert "mgr-secret" not in blob
    assert "password" not in blob


async def test_put_distributed_happy_path(
    client: AsyncClient,
    seed_superuser: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_probe_stubs(monkeypatch)
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200

    resp = await client.put("/api/v1/superuser/wazuh-topology", json=_DISTRIBUTED)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "distributed"
    # 2 indexer nodes + master + 2 dashboards + 2 workers = 7 probes total.
    assert len(body["probe_results"]) == 7
    assert body["warnings"] == []


async def test_put_distributed_dashboard_failure_blocks(
    client: AsyncClient,
    seed_superuser: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A listed dashboard is a blocker (like the single-host dashboard).
    _install_probe_stubs(monkeypatch, fail_urls=frozenset({"https://dash2:443"}))
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200
    resp = await client.put("/api/v1/superuser/wazuh-topology", json=_DISTRIBUTED)
    assert resp.status_code == 400


async def test_put_distributed_worker_failure_is_warning_not_block(
    client: AsyncClient,
    seed_superuser: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_probe_stubs(monkeypatch, fail_urls=frozenset({"https://worker2:55000"}))
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200

    resp = await client.put("/api/v1/superuser/wazuh-topology", json=_DISTRIBUTED)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is True
    assert len(body["warnings"]) == 1  # the down worker is a warning, not a blocker


# ── PUT hard-fail paths ──────────────────────────────────────────────────────


async def test_put_blocked_when_indexer_fails(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_probe_stubs(monkeypatch, fail_urls=frozenset({"https://wazuh.example:9200"}))
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200

    resp = await client.put("/api/v1/superuser/wazuh-topology", json=_SINGLE)
    assert resp.status_code == 400

    # Nothing persisted — GET still reports unconfigured.
    assert (await client.get("/api/v1/superuser/wazuh-topology")).json()["configured"] is False
    assert await db.scalar(select(WazuhEcosystemTopology)) is None
    # The rejected attempt IS audited (security-relevant).
    event = await db.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "install.wazuh_topology.probe_failed")
    )
    assert event is not None


async def test_put_blocked_when_dashboard_fails(
    client: AsyncClient,
    seed_superuser: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_probe_stubs(monkeypatch, fail_urls=frozenset({"https://wazuh.example:443"}))
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200
    resp = await client.put("/api/v1/superuser/wazuh-topology", json=_SINGLE)
    assert resp.status_code == 400


# ── Credential keep-existing semantics ───────────────────────────────────────


async def test_put_first_save_requires_password(
    client: AsyncClient,
    seed_superuser: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_probe_stubs(monkeypatch)
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200

    payload = {**_SINGLE, "indexer_admin_password": None}
    resp = await client.put("/api/v1/superuser/wazuh-topology", json=payload)
    assert resp.status_code == 422
    assert "password is required" in resp.text.lower()


async def test_put_keeps_existing_password_when_omitted(
    client: AsyncClient,
    seed_superuser: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_probe_stubs(monkeypatch)
    assert (await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)).status_code == 200

    # First save with passwords.
    assert (await client.put("/api/v1/superuser/wazuh-topology", json=_SINGLE)).status_code == 200

    # Edit a URL, omit passwords → still succeeds, stored secret unchanged.
    edited = json.loads(json.dumps(_SINGLE))
    edited["topology"]["dashboard_url"] = "https://wazuh.example:8443"
    edited["indexer_admin_password"] = None
    edited["manager_api_password"] = None
    resp = await client.put("/api/v1/superuser/wazuh-topology", json=edited)
    assert resp.status_code == 200, resp.text

    raw = await get_secrets_backend().get(topo.INDEXER_CREDENTIAL_KEY)
    assert raw is not None and json.loads(raw)["password"] == "idx-secret"
    got = (await client.get("/api/v1/superuser/wazuh-topology")).json()
    assert got["topology"]["dashboard_url"] == "https://wazuh.example:8443"
