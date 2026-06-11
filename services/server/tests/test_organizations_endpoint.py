"""Tests for GET /api/v1/auth/me/organizations — the wolf-dashboard organization switcher."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_server.organization.models import Organization, User, UserOrganization


@pytest.mark.asyncio
async def test_my_organizations_requires_authentication(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/auth/me/organizations")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_my_organizations_returns_only_user_memberships(
    client: AsyncClient,
    db: AsyncSession,
    seed_organization_and_user: dict[str, Any],
) -> None:
    """A user sees only their own organization memberships, never others'."""
    # Add a SECOND organization the user belongs to.
    other_organization = Organization(
        id=uuid.uuid4(),
        name="Other Corp",
        slug=f"other-{uuid.uuid4().hex[:8]}",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(other_organization)
    db.add(
        UserOrganization(
            id=uuid.uuid4(),
            user_id=seed_organization_and_user["user_id"],
            organization_id=other_organization.id,
            role="analyst",
            created_at=datetime.now(UTC),
        )
    )

    # Add a THIRD organization the user does NOT belong to.
    foreign_organization = Organization(
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
    db.add(foreign_organization)
    db.add(foreign_user)
    db.add(
        UserOrganization(
            id=uuid.uuid4(),
            user_id=foreign_user.id,
            organization_id=foreign_organization.id,
            role="analyst",
            created_at=datetime.now(UTC),
        )
    )
    await db.commit()

    # Login as the original user (who now has 2 memberships).
    await client.post(
        "/api/v1/auth/login",
        json={
            "email": seed_organization_and_user["user_email"],
            "password": "password123",
            "organization_id": str(seed_organization_and_user["organization_id"]),
        },
    )

    resp = await client.get("/api/v1/auth/me/organizations")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    returned_slugs = {row["slug"] for row in rows}
    assert seed_organization_and_user["organization_slug"] in returned_slugs
    assert other_organization.slug in returned_slugs
    assert foreign_organization.slug not in returned_slugs
    # Each row carries the user's role within that organization.
    for row in rows:
        assert row["role"] in {"analyst", "responder", "engineer", "admin", "superuser"}


@pytest.mark.asyncio
async def test_my_organizations_excludes_inactive_organizations(
    client: AsyncClient,
    db: AsyncSession,
    seed_organization_and_user: dict[str, Any],
) -> None:
    """Inactive organizations must not appear in the switcher."""
    # Mark the seeded organization inactive AFTER login (so the auth cookie exists)
    # but BEFORE the /me/organizations call.
    await client.post(
        "/api/v1/auth/login",
        json={
            "email": seed_organization_and_user["user_email"],
            "password": "password123",
            "organization_id": str(seed_organization_and_user["organization_id"]),
        },
    )
    organization = await db.get(Organization, seed_organization_and_user["organization_id"])
    assert organization is not None
    organization.is_active = False
    await db.commit()

    resp = await client.get("/api/v1/auth/me/organizations")
    assert resp.status_code == 200
    slugs = [row["slug"] for row in resp.json()]
    assert seed_organization_and_user["organization_slug"] not in slugs
