"""Tests for Phase 6.5-b — role enforcement (ADR 0018, Phase 6.5 subset).

Covers:
  - the capability matrix in organization/rbac.py matches ADR 0018 row
    for row (the non-Phase-6 rows)
  - org CRUD is Superuser-only (create / list / rename / soft-delete)
  - org user management is Admin-only; roles assignable by an Admin
    exclude "superuser"; dual audit (org + install) on every mutation
  - the "Last Admin" invariant guard (demote + remove paths)
  - Superuser-membership consent gate (grant / revoke, Admin-only)
  - the org audit-log view (Admin + Responder; Analyst/Engineer refused;
    scoped to the caller's org, install-level events excluded)
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_server.audit.models import AuditEvent
from wolf_server.auth.local import hash_password
from wolf_server.bootstrap.superuser import SUPERUSER_EMAIL, SUPERUSER_USERNAME
from wolf_server.organization.context import VALID_ROLES
from wolf_server.organization.models import Organization, User, UserOrganization
from wolf_server.organization.rbac import (
    ROLE_CAPABILITIES,
    Capability,
    role_has_capability,
)

_WOLF_PASSWORD = "test-wolf-password-32-chars-long!!"
_MEMBER_PASSWORD = "password123"


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _new_user(email: str, *, is_superuser: bool = False) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid4(),
        email=email,
        display_name=email.split("@")[0],
        hashed_password=hash_password(_MEMBER_PASSWORD),
        is_active=True,
        is_superuser=is_superuser,
        created_at=now,
        updated_at=now,
    )


def _new_binding(user_id: uuid.UUID, organization_id: uuid.UUID, role: str) -> UserOrganization:
    return UserOrganization(
        id=uuid.uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        role=role,
        created_at=datetime.now(UTC),
    )


@pytest_asyncio.fixture
async def org_with_members(db: AsyncSession) -> dict[str, Any]:
    """An org with one member of each role (admin/engineer/responder/analyst)."""
    suffix = uuid.uuid4().hex[:8]
    now = datetime.now(UTC)
    org = Organization(
        id=uuid.uuid4(),
        name=f"RBAC Corp {suffix}",
        slug=f"rbac-corp-{suffix}",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(org)

    members: dict[str, dict[str, Any]] = {}
    for role in ("admin", "engineer", "responder", "analyst"):
        user = _new_user(f"{role}-{suffix}@test.example")
        db.add(user)
        db.add(_new_binding(user.id, org.id, role))
        members[role] = {"user_id": user.id, "email": user.email}
    await db.commit()

    return {"organization_id": org.id, "slug": org.slug, **members}


@pytest_asyncio.fixture
async def seed_superuser(db: AsyncSession) -> dict[str, Any]:
    """Insert the bootstrap Superuser into the shared test DB (idempotent)."""
    existing = await db.scalar(select(User).where(User.email == SUPERUSER_EMAIL))
    if existing is None:
        existing = _new_user(SUPERUSER_EMAIL, is_superuser=True)
        existing.display_name = SUPERUSER_USERNAME
        existing.hashed_password = hash_password(_WOLF_PASSWORD)
        db.add(existing)
        await db.commit()
    return {"user_id": existing.id, "email": existing.email}


async def _login_as(
    client: AsyncClient,
    org: dict[str, Any],
    role: str,
) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={
            "email": org[role]["email"],
            "password": _MEMBER_PASSWORD,
        },
    )
    assert resp.status_code == 200, resp.text
    # Auth-only session (ADR 0018): name the org on every request via
    # the header — set as a client default for the rest of the test.
    client.headers["X-Organization-Id"] = str(org["organization_id"])


async def _login_superuser(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": SUPERUSER_USERNAME, "password": _WOLF_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    # The Superuser session is org-less; drop any header a previous
    # member login in the same test left as a client default.
    client.headers.pop("X-Organization-Id", None)


async def _audit_events(db: AsyncSession, event_type: str) -> list[AuditEvent]:
    db.expire_all()
    result = await db.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == event_type)
        .order_by(AuditEvent.created_at)
    )
    return list(result.scalars())


# ─── The capability matrix itself ────────────────────────────────────────────


def test_valid_roles_match_adr_0018() -> None:
    assert {"analyst", "responder", "engineer", "admin", "superuser"} == VALID_ROLES
    assert "approver" not in VALID_ROLES


def test_capability_matrix_matches_adr_0018() -> None:
    """Row-for-row mirror of the ADR 0018 capability matrix (6.5-b subset)."""
    baseline = {Capability.CHAT, Capability.DATA_READ}
    assert ROLE_CAPABILITIES["admin"] == baseline | {
        Capability.SUPERUSER_MEMBERSHIP_GRANT,
        Capability.USERS_MANAGE,
        Capability.ORG_SETTINGS_CONFIGURE,
        Capability.WOLF_PACK_DEPLOY,
        Capability.AUDIT_LOG_VIEW,
    }
    assert ROLE_CAPABILITIES["engineer"] == baseline | {
        Capability.ORG_SETTINGS_CONFIGURE,
        Capability.WOLF_PACK_DEPLOY,
    }
    assert ROLE_CAPABILITIES["responder"] == baseline | {Capability.AUDIT_LOG_VIEW}
    assert ROLE_CAPABILITIES["analyst"] == baseline
    # The Superuser's consented membership: read + chat, no governance.
    assert ROLE_CAPABILITIES["superuser"] == baseline
    assert set(ROLE_CAPABILITIES) == VALID_ROLES


def test_unknown_role_has_no_capabilities() -> None:
    assert not role_has_capability("hacker", Capability.CHAT)
    assert not role_has_capability("", Capability.DATA_READ)


# ─── Org CRUD (Superuser-only) ───────────────────────────────────────────────


async def test_org_crud_requires_superuser(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    # Unauthenticated.
    assert (await client.get("/api/v1/organizations")).status_code == 401
    assert (
        await client.post("/api/v1/organizations", json={"name": "X", "slug": "x-org"})
    ).status_code == 401

    # An org Admin is still not the install Superuser.
    await _login_as(client, org_with_members, "admin")
    assert (await client.get("/api/v1/organizations")).status_code == 403
    assert (
        await client.post("/api/v1/organizations", json={"name": "X", "slug": "x-org"})
    ).status_code == 403
    org_id = org_with_members["organization_id"]
    assert (await client.delete(f"/api/v1/organizations/{org_id}")).status_code == 403


async def test_superuser_creates_lists_renames_and_deletes_org(
    client: AsyncClient, db: AsyncSession, seed_superuser: dict[str, Any]
) -> None:
    await _login_superuser(client)
    suffix = uuid.uuid4().hex[:8]

    resp = await client.post(
        "/api/v1/organizations", json={"name": "Acme Corp", "slug": f"acme-{suffix}"}
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["is_active"] is True
    org_id = created["id"]

    listed = await client.get("/api/v1/organizations")
    assert listed.status_code == 200
    assert org_id in {row["id"] for row in listed.json()}

    resp = await client.patch(f"/api/v1/organizations/{org_id}", json={"name": "Acme Renamed"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Acme Renamed"
    assert resp.json()["slug"] == f"acme-{suffix}"  # slug immutable

    resp = await client.delete(f"/api/v1/organizations/{org_id}")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    # Soft-delete: the row survives, deactivated.
    org = await db.scalar(select(Organization).where(Organization.id == uuid.UUID(org_id)))
    assert org is not None
    await db.refresh(org)
    assert org.is_active is False

    # Second delete refused.
    assert (await client.delete(f"/api/v1/organizations/{org_id}")).status_code == 409

    # Full audit trail, attributed to the Superuser.
    for event_type in ("organization.created", "organization.updated", "organization.deleted"):
        events = await _audit_events(db, event_type)
        match = [e for e in events if e.organization_id == uuid.UUID(org_id)]
        assert match, f"missing audit event {event_type}"
        assert match[-1].user_id == seed_superuser["user_id"]


async def test_create_org_duplicate_slug_and_bad_slug_refused(
    client: AsyncClient, seed_superuser: dict[str, Any]
) -> None:
    await _login_superuser(client)
    suffix = uuid.uuid4().hex[:8]
    slug = f"dup-{suffix}"

    assert (
        await client.post("/api/v1/organizations", json={"name": "One", "slug": slug})
    ).status_code == 201
    assert (
        await client.post("/api/v1/organizations", json={"name": "Two", "slug": slug})
    ).status_code == 409

    for bad_slug in ("UPPER", "has space", "-leading", "trailing-", "ünïcode"):
        resp = await client.post("/api/v1/organizations", json={"name": "Bad", "slug": bad_slug})
        assert resp.status_code == 422, bad_slug


async def test_patch_and_delete_unknown_org_404(
    client: AsyncClient, seed_superuser: dict[str, Any]
) -> None:
    await _login_superuser(client)
    missing = uuid.uuid4()
    assert (
        await client.patch(f"/api/v1/organizations/{missing}", json={"name": "X"})
    ).status_code == 404
    assert (await client.delete(f"/api/v1/organizations/{missing}")).status_code == 404


# ─── Org user management (Admin-only) ────────────────────────────────────────


async def test_user_management_requires_admin(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    for role in ("engineer", "responder", "analyst"):
        await _login_as(client, org_with_members, role)
        assert (await client.get("/api/v1/organization/users")).status_code == 403, role
        resp = await client.post(
            "/api/v1/organization/users",
            json={"email": "x@test.example", "display_name": "X", "role": "analyst"},
        )
        assert resp.status_code == 403, role


async def test_admin_lists_members_with_roles(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    await _login_as(client, org_with_members, "admin")
    resp = await client.get("/api/v1/organization/users")
    assert resp.status_code == 200
    rows = resp.json()
    by_email = {row["email"]: row["role"] for row in rows}
    for role in ("admin", "engineer", "responder", "analyst"):
        assert by_email[org_with_members[role]["email"]] == role


async def test_admin_creates_member_with_generated_password_and_dual_audit(
    client: AsyncClient, db: AsyncSession, org_with_members: dict[str, Any]
) -> None:
    await _login_as(client, org_with_members, "admin")
    email = f"new-{uuid.uuid4().hex[:8]}@test.example"

    resp = await client.post(
        "/api/v1/organization/users",
        json={"email": email, "display_name": "New Analyst", "role": "analyst"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["role"] == "analyst"
    assert body["new_password"] is not None
    assert len(body["new_password"]) >= 32

    # Dual audit: one org-scoped event + one install-level event linked to it.
    events = await _audit_events(db, "organization.member.added")
    org_id = org_with_members["organization_id"]
    org_events = [e for e in events if e.organization_id == org_id]
    assert org_events
    install_events = [
        e for e in events if e.organization_id is None and e.related_event_id == org_events[-1].id
    ]
    assert install_events
    assert install_events[-1].event_data is not None
    assert install_events[-1].event_data["organization_id"] == str(org_id)

    # The new credential works (single membership → auto-select shape).
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": body["new_password"]},
    )
    assert login.status_code == 200
    assert login.json()["auto_selected_organization"]["organization_id"] == str(org_id)


async def test_admin_cannot_assign_superuser_role(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    await _login_as(client, org_with_members, "admin")
    resp = await client.post(
        "/api/v1/organization/users",
        json={"email": "esc@test.example", "display_name": "Esc", "role": "superuser"},
    )
    assert resp.status_code == 422

    analyst_id = org_with_members["analyst"]["user_id"]
    resp = await client.patch(
        f"/api/v1/organization/users/{analyst_id}/role", json={"role": "superuser"}
    )
    assert resp.status_code == 422


async def test_admin_cannot_add_the_superuser_as_ordinary_member(
    client: AsyncClient,
    db: AsyncSession,
    org_with_members: dict[str, Any],
    seed_superuser: dict[str, Any],
) -> None:
    await _login_as(client, org_with_members, "admin")

    # Layer 1: the Superuser's reserved address (wolf@wolf.local) is a
    # special-use domain that EmailStr refuses at validation time — the
    # bootstrap identity cannot even be expressed through this endpoint.
    resp = await client.post(
        "/api/v1/organization/users",
        json={"email": SUPERUSER_EMAIL, "display_name": "Wolf", "role": "admin"},
    )
    assert resp.status_code == 422

    # Layer 2: the explicit is_superuser guard, exercised with a
    # superuser-flagged account that carries a routable address.
    routable_su = _new_user(
        f"routable-su-{uuid.uuid4().hex[:8]}@test.example", is_superuser=True
    )
    db.add(routable_su)
    await db.commit()
    try:
        resp = await client.post(
            "/api/v1/organization/users",
            json={"email": routable_su.email, "display_name": "Wolf", "role": "admin"},
        )
        assert resp.status_code == 409
        assert "consent-gate" in resp.json()["detail"]
    finally:
        # Remove the extra superuser so the consent-gate endpoints (which
        # require exactly ONE active Superuser) see the default state.
        await db.delete(routable_su)
        await db.commit()


async def test_adding_existing_member_again_409(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    await _login_as(client, org_with_members, "admin")
    resp = await client.post(
        "/api/v1/organization/users",
        json={
            "email": org_with_members["analyst"]["email"],
            "display_name": "Dup",
            "role": "analyst",
        },
    )
    assert resp.status_code == 409


async def test_admin_changes_member_role_with_dual_audit(
    client: AsyncClient, db: AsyncSession, org_with_members: dict[str, Any]
) -> None:
    await _login_as(client, org_with_members, "admin")
    analyst_id = org_with_members["analyst"]["user_id"]

    resp = await client.patch(
        f"/api/v1/organization/users/{analyst_id}/role", json={"role": "responder"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "responder"

    binding = await db.scalar(
        select(UserOrganization).where(
            UserOrganization.user_id == analyst_id,
            UserOrganization.organization_id == org_with_members["organization_id"],
        )
    )
    assert binding is not None
    await db.refresh(binding)
    assert binding.role == "responder"

    events = await _audit_events(db, "organization.member.role_changed")
    org_events = [e for e in events if e.organization_id == org_with_members["organization_id"]]
    assert org_events
    assert org_events[-1].event_data is not None
    assert org_events[-1].event_data["old_role"] == "analyst"
    assert org_events[-1].event_data["new_role"] == "responder"
    assert any(
        e.organization_id is None and e.related_event_id == org_events[-1].id for e in events
    )


async def test_role_change_for_non_member_404(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    await _login_as(client, org_with_members, "admin")
    resp = await client.patch(
        f"/api/v1/organization/users/{uuid.uuid4()}/role", json={"role": "analyst"}
    )
    assert resp.status_code == 404


async def test_last_admin_cannot_be_demoted_or_removed(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    await _login_as(client, org_with_members, "admin")
    admin_id = org_with_members["admin"]["user_id"]

    resp = await client.patch(
        f"/api/v1/organization/users/{admin_id}/role", json={"role": "analyst"}
    )
    assert resp.status_code == 409
    assert "last active Admin" in resp.json()["detail"]

    resp = await client.delete(f"/api/v1/organization/users/{admin_id}")
    assert resp.status_code == 409
    assert "last active Admin" in resp.json()["detail"]


async def test_admin_demotion_allowed_once_second_admin_exists(
    client: AsyncClient, db: AsyncSession, org_with_members: dict[str, Any]
) -> None:
    await _login_as(client, org_with_members, "admin")
    engineer_id = org_with_members["engineer"]["user_id"]
    admin_id = org_with_members["admin"]["user_id"]

    # Promote the engineer to Admin, then the original Admin may step down.
    resp = await client.patch(
        f"/api/v1/organization/users/{engineer_id}/role", json={"role": "admin"}
    )
    assert resp.status_code == 200

    resp = await client.patch(
        f"/api/v1/organization/users/{admin_id}/role", json={"role": "analyst"}
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "analyst"


async def test_admin_removes_member(
    client: AsyncClient, db: AsyncSession, org_with_members: dict[str, Any]
) -> None:
    await _login_as(client, org_with_members, "admin")
    analyst_id = org_with_members["analyst"]["user_id"]

    resp = await client.delete(f"/api/v1/organization/users/{analyst_id}")
    assert resp.status_code == 204

    db.expire_all()
    binding = await db.scalar(
        select(UserOrganization).where(
            UserOrganization.user_id == analyst_id,
            UserOrganization.organization_id == org_with_members["organization_id"],
        )
    )
    assert binding is None
    # The account itself survives — only the membership is gone.
    user = await db.scalar(select(User).where(User.id == analyst_id))
    assert user is not None

    events = await _audit_events(db, "organization.member.removed")
    assert any(e.organization_id == org_with_members["organization_id"] for e in events)

    resp = await client.delete(f"/api/v1/organization/users/{analyst_id}")
    assert resp.status_code == 404


# ─── Superuser-membership consent gate ───────────────────────────────────────


async def test_superuser_membership_grant_requires_admin(
    client: AsyncClient, org_with_members: dict[str, Any], seed_superuser: dict[str, Any]
) -> None:
    for role in ("engineer", "responder", "analyst"):
        await _login_as(client, org_with_members, role)
        assert (
            await client.post("/api/v1/organization/memberships/superuser")
        ).status_code == 403, role
        assert (
            await client.delete("/api/v1/organization/memberships/superuser")
        ).status_code == 403, role


async def test_admin_grants_and_revokes_superuser_membership(
    client: AsyncClient,
    db: AsyncSession,
    org_with_members: dict[str, Any],
    seed_superuser: dict[str, Any],
) -> None:
    await _login_as(client, org_with_members, "admin")
    org_id = org_with_members["organization_id"]

    resp = await client.post("/api/v1/organization/memberships/superuser")
    assert resp.status_code == 201, resp.text
    assert resp.json()["role"] == "superuser"
    assert resp.json()["user_id"] == str(seed_superuser["user_id"])

    db.expire_all()
    binding = await db.scalar(
        select(UserOrganization).where(
            UserOrganization.user_id == seed_superuser["user_id"],
            UserOrganization.organization_id == org_id,
        )
    )
    assert binding is not None
    assert binding.role == "superuser"

    # Granting twice is refused.
    assert (await client.post("/api/v1/organization/memberships/superuser")).status_code == 409

    grant_events = await _audit_events(db, "organization.superuser_membership.granted")
    org_grants = [e for e in grant_events if e.organization_id == org_id]
    assert org_grants
    assert any(
        e.organization_id is None and e.related_event_id == org_grants[-1].id for e in grant_events
    )

    resp = await client.delete("/api/v1/organization/memberships/superuser")
    assert resp.status_code == 204

    db.expire_all()
    binding = await db.scalar(
        select(UserOrganization).where(
            UserOrganization.user_id == seed_superuser["user_id"],
            UserOrganization.organization_id == org_id,
        )
    )
    assert binding is None

    # Revoking when absent is a 404.
    assert (await client.delete("/api/v1/organization/memberships/superuser")).status_code == 404

    revoke_events = await _audit_events(db, "organization.superuser_membership.revoked")
    assert any(e.organization_id == org_id for e in revoke_events)


async def test_superuser_binding_role_is_locked(
    client: AsyncClient,
    db: AsyncSession,
    org_with_members: dict[str, Any],
    seed_superuser: dict[str, Any],
) -> None:
    await _login_as(client, org_with_members, "admin")
    assert (await client.post("/api/v1/organization/memberships/superuser")).status_code == 201

    su_id = seed_superuser["user_id"]
    resp = await client.patch(f"/api/v1/organization/users/{su_id}/role", json={"role": "admin"})
    assert resp.status_code == 409

    resp = await client.delete(f"/api/v1/organization/users/{su_id}")
    assert resp.status_code == 409
    assert "memberships/superuser" in resp.json()["detail"]

    # Clean up the grant so other tests see the default zero-membership state.
    assert (await client.delete("/api/v1/organization/memberships/superuser")).status_code == 204


# ─── Org audit-log view ──────────────────────────────────────────────────────


async def test_audit_view_role_gating(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    for role, expected in (("admin", 200), ("responder", 200), ("engineer", 403), ("analyst", 403)):
        await _login_as(client, org_with_members, role)
        resp = await client.get("/api/v1/organization/audit")
        assert resp.status_code == expected, role


async def test_audit_view_is_scoped_to_own_org(
    client: AsyncClient,
    db: AsyncSession,
    org_with_members: dict[str, Any],
) -> None:
    org_id = org_with_members["organization_id"]
    foreign_org_id = uuid.uuid4()
    db.add(
        AuditEvent(
            event_type="rbac.test.own_org",
            event_data={"marker": "own"},
            organization_id=org_id,
        )
    )
    db.add(
        AuditEvent(
            event_type="rbac.test.foreign_org",
            event_data={"marker": "foreign"},
            organization_id=foreign_org_id,
        )
    )
    db.add(
        AuditEvent(
            event_type="rbac.test.install_level",
            event_data={"marker": "install"},
            organization_id=None,
        )
    )
    await db.commit()

    await _login_as(client, org_with_members, "responder")
    resp = await client.get("/api/v1/organization/audit", params={"limit": 200})
    assert resp.status_code == 200
    types = {e["event_type"] for e in resp.json()["events"]}
    assert "rbac.test.own_org" in types
    assert "rbac.test.foreign_org" not in types
    assert "rbac.test.install_level" not in types


async def test_audit_view_pagination(
    client: AsyncClient, db: AsyncSession, org_with_members: dict[str, Any]
) -> None:
    org_id = org_with_members["organization_id"]
    for i in range(5):
        db.add(
            AuditEvent(
                event_type="rbac.test.page",
                event_data={"i": i},
                organization_id=org_id,
            )
        )
    await db.commit()

    await _login_as(client, org_with_members, "admin")
    resp = await client.get("/api/v1/organization/audit", params={"limit": 2, "offset": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["events"]) == 2
    assert body["limit"] == 2

    # limit caps at 200.
    resp = await client.get("/api/v1/organization/audit", params={"limit": 500})
    assert resp.status_code == 422


# ─── Chat capability gate regression ─────────────────────────────────────────


async def test_every_org_role_keeps_chat_access_gate(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    """CHAT is in every role's baseline — the new gate must not lock anyone
    out.  401/403 would mean the capability wiring broke membership access;
    the chat route itself needs a model provider, so any non-auth failure
    mode (422 for the empty body here) proves the gate let the request in.
    """
    for role in ("admin", "engineer", "responder", "analyst"):
        await _login_as(client, org_with_members, role)
        resp = await client.post("/api/v1/chat", json={})
        assert resp.status_code not in (401, 403), role
