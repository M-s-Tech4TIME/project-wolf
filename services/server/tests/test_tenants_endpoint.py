"""Tests for GET /api/v1/auth/me/tenants — the wolf-dashboard tenant switcher."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_server.tenancy.models import Tenant, User, UserTenant


@pytest.mark.asyncio
async def test_my_tenants_requires_authentication(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/auth/me/tenants")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_my_tenants_returns_only_user_memberships(
    client: AsyncClient,
    db: AsyncSession,
    seed_tenant_and_user: dict[str, Any],
) -> None:
    """A user sees only their own tenant memberships, never others'."""
    # Add a SECOND tenant the user belongs to.
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other Corp",
        slug=f"other-{uuid.uuid4().hex[:8]}",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(other_tenant)
    db.add(UserTenant(
        id=uuid.uuid4(),
        user_id=seed_tenant_and_user["user_id"],
        tenant_id=other_tenant.id,
        role="analyst",
        created_at=datetime.now(UTC),
    ))

    # Add a THIRD tenant the user does NOT belong to.
    foreign_tenant = Tenant(
        id=uuid.uuid4(),
        name="Foreign Corp",
        slug=f"foreign-{uuid.uuid4().hex[:8]}",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    foreign_user = User(
        id=uuid.uuid4(),
        email=f"foreign-{uuid.uuid4().hex[:8]}@x.example",
        display_name="Foreign User",
        hashed_password="$2b$12$dummy",  # noqa: S106 — test fixture
        is_active=True,
        is_superuser=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(foreign_tenant)
    db.add(foreign_user)
    db.add(UserTenant(
        id=uuid.uuid4(),
        user_id=foreign_user.id,
        tenant_id=foreign_tenant.id,
        role="analyst",
        created_at=datetime.now(UTC),
    ))
    await db.commit()

    # Login as the original user (who now has 2 memberships).
    await client.post(
        "/api/v1/auth/login",
        json={
            "email": seed_tenant_and_user["user_email"],
            "password": "password123",
            "tenant_id": str(seed_tenant_and_user["tenant_id"]),
        },
    )

    resp = await client.get("/api/v1/auth/me/tenants")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    returned_slugs = {row["slug"] for row in rows}
    assert seed_tenant_and_user["tenant_slug"] in returned_slugs
    assert other_tenant.slug in returned_slugs
    assert foreign_tenant.slug not in returned_slugs
    # Each row carries the user's role within that tenant.
    for row in rows:
        assert row["role"] in {"analyst", "approver", "admin", "superuser"}


@pytest.mark.asyncio
async def test_my_tenants_excludes_inactive_tenants(
    client: AsyncClient,
    db: AsyncSession,
    seed_tenant_and_user: dict[str, Any],
) -> None:
    """Inactive tenants must not appear in the switcher."""
    # Mark the seeded tenant inactive AFTER login (so the auth cookie exists)
    # but BEFORE the /me/tenants call.
    await client.post(
        "/api/v1/auth/login",
        json={
            "email": seed_tenant_and_user["user_email"],
            "password": "password123",
            "tenant_id": str(seed_tenant_and_user["tenant_id"]),
        },
    )
    tenant = await db.get(Tenant, seed_tenant_and_user["tenant_id"])
    assert tenant is not None
    tenant.is_active = False
    await db.commit()

    resp = await client.get("/api/v1/auth/me/tenants")
    assert resp.status_code == 200
    slugs = [row["slug"] for row in resp.json()]
    assert seed_tenant_and_user["tenant_slug"] not in slugs
