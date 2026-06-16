"""Tests for Phase 6.5-d — install-wide audit endpoint.

GET /api/v1/superuser/audit (Superuser-only) returns the audit trail
across every organization PLUS system-level rows (organization_id IS
NULL), newest first, paginated. Distinct from the org-scoped
GET /api/v1/organization/audit (org_management), which filters to one
org and excludes system-level rows.

Covers:
  - unauthenticated → 401; non-superuser (analyst) → 403
  - Superuser sees events from MULTIPLE orgs + system-level rows in one
    view; organization_name is populated for org events and null for
    system-level events
  - newest-first ordering + limit/offset pagination
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_server.audit.log import write_event
from wolf_server.auth.local import hash_password
from wolf_server.bootstrap.superuser import SUPERUSER_EMAIL, SUPERUSER_USERNAME
from wolf_server.organization.models import Organization, User

_WOLF_PASSWORD = "test-wolf-password-32-chars-long!!"


@pytest_asyncio.fixture
async def seed_superuser(db: AsyncSession) -> dict[str, Any]:
    """Insert the bootstrap Superuser into the shared test DB."""
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
    return {"user_id": existing.id, "email": existing.email}


async def _login(client: AsyncClient, email: str, password: str) -> Any:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def _make_org(db: AsyncSession, name: str, slug_suffix: str) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
        name=name,
        slug=f"audit-{slug_suffix}-{uuid.uuid4().hex[:8]}",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(org)
    await db.commit()
    return org


# ─── Authorization ─────────────────────────────────────────────────────────


async def test_install_audit_unauthenticated_rejected(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/superuser/audit")
    assert resp.status_code == 401


async def test_install_audit_requires_superuser(
    client: AsyncClient, seed_organization_and_user: dict[str, Any]
) -> None:
    # Authenticated as an ordinary analyst — must be refused.
    resp = await _login(client, seed_organization_and_user["user_email"], "password123")
    assert resp.status_code == 200

    resp = await client.get("/api/v1/superuser/audit")
    assert resp.status_code == 403


# ─── Cross-org + system-level visibility ────────────────────────────────────


async def test_install_audit_returns_cross_org_and_system_level(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
) -> None:
    org_a = await _make_org(db, "Org Alpha", "alpha")
    org_b = await _make_org(db, "Org Beta", "beta")

    await write_event(db, event_type="test.event.a", organization_id=org_a.id)
    await write_event(db, event_type="test.event.b", organization_id=org_b.id)
    # Install-level row: no organization.
    await write_event(db, event_type="test.event.system", organization_id=None)
    await db.commit()

    resp = await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)
    assert resp.status_code == 200

    resp = await client.get("/api/v1/superuser/audit")
    assert resp.status_code == 200
    body = resp.json()

    by_type = {e["event_type"]: e for e in body["events"]}
    # All three are visible in the single install-wide view.
    assert {"test.event.a", "test.event.b", "test.event.system"} <= set(by_type)

    # org events carry their organization's name; system-level is null.
    assert by_type["test.event.a"]["organization_id"] == str(org_a.id)
    assert by_type["test.event.a"]["organization_name"] == "Org Alpha"
    assert by_type["test.event.b"]["organization_name"] == "Org Beta"
    assert by_type["test.event.system"]["organization_id"] is None
    assert by_type["test.event.system"]["organization_name"] is None


# ─── Ordering + pagination ───────────────────────────────────────────────────


async def test_install_audit_newest_first_and_pagination(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
) -> None:
    org = await _make_org(db, "Org Paginate", "page")
    # Insert in a known order; created_at defaults are monotonic per write.
    for i in range(5):
        await write_event(
            db,
            event_type=f"test.page.{i}",
            organization_id=org.id,
            event_data={"seq": i},
        )
    await db.commit()

    resp = await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)
    assert resp.status_code == 200

    # First page of 2, newest-first.
    page1 = (await client.get("/api/v1/superuser/audit?limit=2&offset=0")).json()
    assert page1["limit"] == 2
    assert page1["offset"] == 0
    assert len(page1["events"]) == 2

    # Page 2 continues without overlap.
    page2 = (await client.get("/api/v1/superuser/audit?limit=2&offset=2")).json()
    page1_ids = {e["id"] for e in page1["events"]}
    page2_ids = {e["id"] for e in page2["events"]}
    assert page1_ids.isdisjoint(page2_ids)

    # Newest-first: within our seeded set, the last-written seq should
    # appear ahead of the first-written one across the full result.
    full = (await client.get("/api/v1/superuser/audit?limit=200&offset=0")).json()
    seqs = [
        e["event_data"]["seq"]
        for e in full["events"]
        if e["event_type"].startswith("test.page.")
    ]
    assert seqs == sorted(seqs, reverse=True)
