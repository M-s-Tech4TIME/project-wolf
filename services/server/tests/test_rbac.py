"""Tests for Phase 6.5-b — role enforcement (ADR 0018, Phase 6.5 subset).

Covers:
  - the capability matrix in organization/rbac.py matches ADR 0018 row
    for row (the non-Phase-6 rows)
  - org CRUD is Superuser-only (create / list / rename / soft-delete)
  - org user management is Admin-only; roles assignable by an Admin
    exclude "superuser"; dual audit (org + install) on every mutation
  - the "Last Admin" invariant guard (demote + remove paths)
  - Superuser-membership consent gate (6.5-f): request (Superuser) →
    approve/reject (Admin) → time-limited grant → lazy expiry / revoke,
    with the all-member transparency banner + cross-org isolation
  - the org audit-log view (Admin + Responder; Analyst/Engineer refused;
    scoped to the caller's org, install-level events excluded)
"""

import uuid
from datetime import UTC, datetime, timedelta
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


def _new_user(
    email: str, *, is_superuser: bool = False, verification_status: str = "verified"
) -> User:
    # Seeded test members are "verified" by default — they stand in for
    # already-onboarded users (mirrors migration 0010's backfill of
    # pre-existing rows).  Pass verification_status="unverified" to model a
    # freshly invited account for the 6.5-h gate/flow tests.
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid4(),
        email=email,
        display_name=email.split("@")[0],
        hashed_password=hash_password(_MEMBER_PASSWORD),
        is_active=True,
        is_superuser=is_superuser,
        verification_status=verification_status,
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


async def _make_org_with_admin(db: AsyncSession) -> dict[str, Any]:
    """A second org with a single Admin — for cross-organization tests."""
    suffix = uuid.uuid4().hex[:8]
    now = datetime.now(UTC)
    org = Organization(
        id=uuid.uuid4(),
        name=f"Other Corp {suffix}",
        slug=f"other-corp-{suffix}",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(org)
    admin = _new_user(f"admin-{suffix}@test.example")
    db.add(admin)
    db.add(_new_binding(admin.id, org.id, "admin"))
    await db.commit()
    return {"organization_id": org.id, "admin": {"user_id": admin.id, "email": admin.email}}


async def _request_access(
    client: AsyncClient, organization_id: uuid.UUID, **body: Any
) -> dict[str, Any]:
    """Superuser files an access request for an org; returns the request JSON."""
    resp = await client.post(
        f"/api/v1/superuser/organizations/{organization_id}/access-requests",
        json=body,
    )
    assert resp.status_code == 201, resp.text
    result: dict[str, Any] = resp.json()
    return result


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


# ─── Member password reset (Phase 6.5-e.1) ───────────────────────────────────


async def test_password_reset_requires_admin(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    analyst_id = org_with_members["analyst"]["user_id"]
    for role in ("engineer", "responder", "analyst"):
        await _login_as(client, org_with_members, role)
        resp = await client.post(
            f"/api/v1/organization/users/{analyst_id}/password-reset"
        )
        assert resp.status_code == 403, role


async def test_admin_resets_member_password_and_dual_audit(
    client: AsyncClient, db: AsyncSession, org_with_members: dict[str, Any]
) -> None:
    await _login_as(client, org_with_members, "admin")
    analyst = org_with_members["analyst"]

    resp = await client.post(
        f"/api/v1/organization/users/{analyst['user_id']}/password-reset"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == analyst["email"]
    assert len(body["new_password"]) >= 32

    # Dual audit: org-scoped event + linked install-level event.
    events = await _audit_events(db, "organization.member.password_reset")
    org_id = org_with_members["organization_id"]
    org_events = [e for e in events if e.organization_id == org_id]
    assert org_events
    assert org_events[-1].event_data is not None
    assert org_events[-1].event_data["sessions_revoked"] is True
    install_events = [
        e
        for e in events
        if e.organization_id is None and e.related_event_id == org_events[-1].id
    ]
    assert install_events

    # The new credential works; the old one no longer does.
    old = await client.post(
        "/api/v1/auth/login",
        json={"email": analyst["email"], "password": _MEMBER_PASSWORD},
    )
    assert old.status_code == 401
    new = await client.post(
        "/api/v1/auth/login",
        json={"email": analyst["email"], "password": body["new_password"]},
    )
    assert new.status_code == 200


async def test_reset_password_unknown_member_404(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    await _login_as(client, org_with_members, "admin")
    resp = await client.post(
        f"/api/v1/organization/users/{uuid.uuid4()}/password-reset"
    )
    assert resp.status_code == 404


async def test_admin_cannot_reset_superuser_password(
    client: AsyncClient,
    db: AsyncSession,
    org_with_members: dict[str, Any],
    seed_superuser: dict[str, Any],
) -> None:
    # Give the Superuser a (consent-granted) membership in this org, then
    # confirm an Admin still can't rotate that credential via the API.
    org_id = org_with_members["organization_id"]
    db.add(_new_binding(seed_superuser["user_id"], org_id, "superuser"))
    await db.commit()

    await _login_as(client, org_with_members, "admin")
    resp = await client.post(
        f"/api/v1/organization/users/{seed_superuser['user_id']}/password-reset"
    )
    assert resp.status_code == 409


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


async def _superuser_binding(
    db: AsyncSession, user_id: uuid.UUID, organization_id: uuid.UUID
) -> UserOrganization | None:
    db.expire_all()
    return await db.scalar(
        select(UserOrganization).where(
            UserOrganization.user_id == user_id,
            UserOrganization.organization_id == organization_id,
        )
    )


async def test_access_request_requires_superuser(
    client: AsyncClient, org_with_members: dict[str, Any], seed_superuser: dict[str, Any]
) -> None:
    # A regular member is not the Superuser → require_superuser → 403.
    org_id = org_with_members["organization_id"]
    await _login_as(client, org_with_members, "admin")
    resp = await client.post(
        f"/api/v1/superuser/organizations/{org_id}/access-requests", json={}
    )
    assert resp.status_code == 403


async def test_access_request_decisions_require_admin(
    client: AsyncClient, org_with_members: dict[str, Any], seed_superuser: dict[str, Any]
) -> None:
    org_id = org_with_members["organization_id"]
    await _login_superuser(client)
    rid = (await _request_access(client, org_id))["id"]

    # Non-admin members can neither list nor decide nor revoke.
    for role in ("engineer", "responder", "analyst"):
        await _login_as(client, org_with_members, role)
        assert (await client.get("/api/v1/organization/access-requests")).status_code == 403, role
        assert (
            await client.post(f"/api/v1/organization/access-requests/{rid}/approve", json={})
        ).status_code == 403, role
        assert (
            await client.post(f"/api/v1/organization/access-requests/{rid}/reject", json={})
        ).status_code == 403, role
        assert (
            await client.delete("/api/v1/organization/memberships/superuser")
        ).status_code == 403, role

    # Clean up the pending request.
    await _login_superuser(client)
    assert (await client.delete(f"/api/v1/superuser/access-requests/{rid}")).status_code == 204


async def test_request_approve_revoke_flow(
    client: AsyncClient,
    db: AsyncSession,
    org_with_members: dict[str, Any],
    seed_superuser: dict[str, Any],
) -> None:
    org_id = org_with_members["organization_id"]
    su_id = seed_superuser["user_id"]

    # Superuser files a request.
    await _login_superuser(client)
    req = await _request_access(client, org_id, reason="joint debug", requested_duration_hours=24)
    rid = req["id"]
    assert req["status"] == "pending"
    assert req["currently_active"] is False

    # Admin sees it pending in the consent-gate inbox.
    await _login_as(client, org_with_members, "admin")
    listing = (await client.get("/api/v1/organization/access-requests")).json()
    assert any(r["id"] == rid and r["status"] == "pending" for r in listing)

    # Approve, honouring the requested 24h.
    resp = await client.post(
        f"/api/v1/organization/access-requests/{rid}/approve", json={"mode": "requested"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "approved"
    assert body["granted_expires_at"] is not None
    # Lifecycle fields: the deciding Admin is named, the grant hasn't ended.
    assert body["decided_by_display_name"]
    assert body["ended_at"] is None

    # The time-limited membership row exists.
    binding = await _superuser_binding(db, su_id, org_id)
    assert binding is not None
    assert binding.role == "superuser"
    assert binding.expires_at is not None

    # The all-member transparency banner shows the active grant.
    banner = (await client.get("/api/v1/organization/superuser-access")).json()
    assert banner is not None
    assert banner["expires_at"] is not None
    assert banner["granted_by_display_name"]

    # Dual audit (org + install) for the grant.
    grant_events = await _audit_events(db, "organization.superuser_membership.granted")
    org_grants = [e for e in grant_events if e.organization_id == org_id]
    assert org_grants
    assert any(
        e.organization_id is None and e.related_event_id == org_grants[-1].id for e in grant_events
    )

    # Approving an already-decided request is refused.
    assert (
        await client.post(f"/api/v1/organization/access-requests/{rid}/approve", json={})
    ).status_code == 409

    # Revoke ends it immediately; the banner clears.
    assert (await client.delete("/api/v1/organization/memberships/superuser")).status_code == 204
    assert await _superuser_binding(db, su_id, org_id) is None
    assert (await client.get("/api/v1/organization/superuser-access")).json() is None
    assert (await client.delete("/api/v1/organization/memberships/superuser")).status_code == 404
    revoke_events = await _audit_events(db, "organization.superuser_membership.revoked")
    assert any(e.organization_id == org_id for e in revoke_events)

    # The request row now records the full lifecycle: approved → revoked,
    # with the terminal timestamp + the deciding Admin still attributed.
    listing2 = (await client.get("/api/v1/organization/access-requests")).json()
    revoked_row = next(r for r in listing2 if r["id"] == rid)
    assert revoked_row["status"] == "revoked"
    assert revoked_row["ended_at"] is not None
    assert revoked_row["decided_by_display_name"]


async def test_reject_flow(
    client: AsyncClient,
    db: AsyncSession,
    org_with_members: dict[str, Any],
    seed_superuser: dict[str, Any],
) -> None:
    org_id = org_with_members["organization_id"]
    su_id = seed_superuser["user_id"]
    await _login_superuser(client)
    rid = (await _request_access(client, org_id))["id"]

    await _login_as(client, org_with_members, "admin")
    resp = await client.post(
        f"/api/v1/organization/access-requests/{rid}/reject", json={"reason": "not now"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    assert await _superuser_binding(db, su_id, org_id) is None
    assert (await client.get("/api/v1/organization/superuser-access")).json() is None
    rejected = await _audit_events(db, "organization.superuser_access.rejected")
    assert any(e.organization_id == org_id for e in rejected)
    # Rejecting again → 409 (no longer pending).
    assert (
        await client.post(f"/api/v1/organization/access-requests/{rid}/reject", json={})
    ).status_code == 409


async def test_superuser_cancels_own_pending(
    client: AsyncClient,
    db: AsyncSession,
    org_with_members: dict[str, Any],
    seed_superuser: dict[str, Any],
) -> None:
    org_id = org_with_members["organization_id"]
    await _login_superuser(client)
    rid = (await _request_access(client, org_id))["id"]

    mine = (await client.get("/api/v1/superuser/access-requests")).json()
    assert any(r["id"] == rid and r["status"] == "pending" for r in mine)

    assert (await client.delete(f"/api/v1/superuser/access-requests/{rid}")).status_code == 204
    # Cancelling again → 409 (already cancelled).
    assert (await client.delete(f"/api/v1/superuser/access-requests/{rid}")).status_code == 409
    cancelled = await _audit_events(db, "organization.superuser_access.cancelled")
    assert any(e.organization_id == org_id for e in cancelled)


async def test_approve_duration_override_and_until_revoked(
    client: AsyncClient,
    db: AsyncSession,
    org_with_members: dict[str, Any],
    seed_superuser: dict[str, Any],
) -> None:
    org_id = org_with_members["organization_id"]
    su_id = seed_superuser["user_id"]

    # "until revoked": Superuser requests open-ended, Admin grants it.
    await _login_superuser(client)
    rid = (await _request_access(client, org_id, requested_duration_hours=None))["id"]
    await _login_as(client, org_with_members, "admin")
    resp = await client.post(
        f"/api/v1/organization/access-requests/{rid}/approve", json={"mode": "until_revoked"}
    )
    assert resp.status_code == 200
    assert resp.json()["granted_expires_at"] is None
    binding = await _superuser_binding(db, su_id, org_id)
    assert binding is not None
    assert binding.expires_at is None
    assert (await client.delete("/api/v1/organization/memberships/superuser")).status_code == 204

    # Override: Superuser asks 24h, Admin grants 1h instead.
    await _login_superuser(client)
    rid2 = (await _request_access(client, org_id, requested_duration_hours=24))["id"]
    await _login_as(client, org_with_members, "admin")
    resp = await client.post(
        f"/api/v1/organization/access-requests/{rid2}/approve",
        json={"mode": "hours", "duration_hours": 1},
    )
    assert resp.status_code == 200
    binding = await _superuser_binding(db, su_id, org_id)
    assert binding is not None
    assert binding.expires_at is not None
    deadline = binding.expires_at
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    seconds = (deadline - datetime.now(UTC)).total_seconds()
    assert 0 < seconds <= 3700  # ~1 hour, not the requested 24
    assert (await client.delete("/api/v1/organization/memberships/superuser")).status_code == 204


async def test_expired_grant_locks_out_superuser(
    client: AsyncClient,
    db: AsyncSession,
    org_with_members: dict[str, Any],
    seed_superuser: dict[str, Any],
) -> None:
    org_id = org_with_members["organization_id"]
    su_id = seed_superuser["user_id"]
    db.add(
        UserOrganization(
            id=uuid.uuid4(),
            user_id=su_id,
            organization_id=org_id,
            role="superuser",
            created_at=datetime.now(UTC) - timedelta(hours=2),
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    await db.commit()

    # The Superuser names the org → context prunes the lapsed grant → 403.
    await _login_superuser(client)
    client.headers["X-Organization-Id"] = str(org_id)
    resp = await client.get("/api/v1/organization/superuser-access")
    assert resp.status_code == 403
    client.headers.pop("X-Organization-Id", None)

    assert await _superuser_binding(db, su_id, org_id) is None
    expired = await _audit_events(db, "organization.superuser_membership.expired")
    assert any(e.organization_id == org_id for e in expired)


async def test_expired_grant_clears_member_banner(
    client: AsyncClient,
    db: AsyncSession,
    org_with_members: dict[str, Any],
    seed_superuser: dict[str, Any],
) -> None:
    org_id = org_with_members["organization_id"]
    su_id = seed_superuser["user_id"]
    db.add(
        UserOrganization(
            id=uuid.uuid4(),
            user_id=su_id,
            organization_id=org_id,
            role="superuser",
            created_at=datetime.now(UTC) - timedelta(hours=2),
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    await db.commit()

    # A regular member's banner poll prunes the lapsed grant + returns null.
    await _login_as(client, org_with_members, "analyst")
    assert (await client.get("/api/v1/organization/superuser-access")).json() is None
    assert await _superuser_binding(db, su_id, org_id) is None


async def test_grant_expiry_marks_request_expired(
    client: AsyncClient,
    db: AsyncSession,
    org_with_members: dict[str, Any],
    seed_superuser: dict[str, Any],
) -> None:
    """A grant born of an approved request that lapses transitions the
    request approved → expired with an ended_at — completing the timeline
    the Admin/Superuser UI renders."""
    org_id = org_with_members["organization_id"]
    su_id = seed_superuser["user_id"]

    # Request → approve (creates an approved request + a time-limited grant).
    await _login_superuser(client)
    rid = (await _request_access(client, org_id, requested_duration_hours=1))["id"]
    await _login_as(client, org_with_members, "admin")
    assert (
        await client.post(
            f"/api/v1/organization/access-requests/{rid}/approve", json={"mode": "requested"}
        )
    ).status_code == 200

    # Force the grant past its deadline, then let a banner poll observe it.
    binding = await _superuser_binding(db, su_id, org_id)
    assert binding is not None
    binding.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db.commit()
    assert (await client.get("/api/v1/organization/superuser-access")).json() is None

    # The request is now terminal: expired, with ended_at + decider intact.
    listing = (await client.get("/api/v1/organization/access-requests")).json()
    row = next(r for r in listing if r["id"] == rid)
    assert row["status"] == "expired"
    assert row["ended_at"] is not None
    assert row["decided_by_display_name"]

    # The Superuser's own view reflects the same terminal state.
    await _login_superuser(client)
    mine = (await client.get("/api/v1/superuser/access-requests")).json()
    my_row = next(r for r in mine if r["id"] == rid)
    assert my_row["status"] == "expired"
    assert my_row["ended_at"] is not None
    assert my_row["currently_active"] is False


async def test_request_conflicts(
    client: AsyncClient,
    db: AsyncSession,
    org_with_members: dict[str, Any],
    seed_superuser: dict[str, Any],
) -> None:
    org_id = org_with_members["organization_id"]
    await _login_superuser(client)
    rid = (await _request_access(client, org_id))["id"]

    # A second pending request for the same org → 409.
    assert (
        await client.post(f"/api/v1/superuser/organizations/{org_id}/access-requests", json={})
    ).status_code == 409

    # Once active, requesting again → 409.
    await _login_as(client, org_with_members, "admin")
    assert (
        await client.post(f"/api/v1/organization/access-requests/{rid}/approve", json={})
    ).status_code == 200
    await _login_superuser(client)
    assert (
        await client.post(f"/api/v1/superuser/organizations/{org_id}/access-requests", json={})
    ).status_code == 409

    await _login_as(client, org_with_members, "admin")
    assert (await client.delete("/api/v1/organization/memberships/superuser")).status_code == 204


async def test_access_request_cross_org_isolation(
    client: AsyncClient,
    db: AsyncSession,
    org_with_members: dict[str, Any],
    seed_superuser: dict[str, Any],
) -> None:
    org_a = org_with_members["organization_id"]
    other = await _make_org_with_admin(db)

    # Superuser requests access to org A only.
    await _login_superuser(client)
    rid_a = (await _request_access(client, org_a))["id"]

    # Org B's Admin cannot see org A's request, nor decide on it (404).
    await _login_as(client, other, "admin")
    listing_b = (await client.get("/api/v1/organization/access-requests")).json()
    assert all(r["id"] != rid_a for r in listing_b)
    assert (
        await client.post(f"/api/v1/organization/access-requests/{rid_a}/approve", json={})
    ).status_code == 404
    assert (
        await client.post(f"/api/v1/organization/access-requests/{rid_a}/reject", json={})
    ).status_code == 404

    # Clean up the pending request.
    await _login_superuser(client)
    assert (await client.delete(f"/api/v1/superuser/access-requests/{rid_a}")).status_code == 204


async def test_superuser_binding_role_is_locked(
    client: AsyncClient,
    db: AsyncSession,
    org_with_members: dict[str, Any],
    seed_superuser: dict[str, Any],
) -> None:
    org_id = org_with_members["organization_id"]
    su_id = seed_superuser["user_id"]
    # Establish the grant via the request → approve flow.
    await _login_superuser(client)
    rid = (await _request_access(client, org_id))["id"]
    await _login_as(client, org_with_members, "admin")
    assert (
        await client.post(f"/api/v1/organization/access-requests/{rid}/approve", json={})
    ).status_code == 200

    # The Superuser's binding can't be promoted or removed via the ordinary
    # user-management routes — only the consent-gate revoke ends it.
    resp = await client.patch(f"/api/v1/organization/users/{su_id}/role", json={"role": "admin"})
    assert resp.status_code == 409

    resp = await client.delete(f"/api/v1/organization/users/{su_id}")
    assert resp.status_code == 409
    # MSSP hygiene: the message states the restriction without leaking the
    # internal endpoint/CLI; it points to revoking access instead (6.5-f).
    detail = resp.json()["detail"]
    assert "revoke their access" in detail
    assert "/api/" not in detail and "memberships/superuser" not in detail

    # Clean up the grant so other tests see the default zero-membership state.
    assert (await client.delete("/api/v1/organization/memberships/superuser")).status_code == 204


async def test_revoke_with_misconfigured_superuser_count_stays_generic(
    client: AsyncClient,
    db: AsyncSession,
    org_with_members: dict[str, Any],
    seed_superuser: dict[str, Any],
) -> None:
    """A violated single-Superuser invariant (0 or 2+ active Superusers)
    surfaces a GENERIC message to the tenant Admin — never the count or the
    bootstrap CLI (MSSP hygiene, 6.5-f). The operator diagnostic is logged
    server-side instead. `_get_install_superuser` runs first in the revoke
    handler, so this fires before any grant lookup (no grant needed)."""
    # Corrupt the singleton with a second active Superuser. The DB is
    # session-scoped + shared, so remove it in a finally even on failure.
    ghost = _new_user("ghost-superuser@wolf.local", is_superuser=True)
    db.add(ghost)
    await db.commit()
    try:
        await _login_as(client, org_with_members, "admin")
        resp = await client.delete("/api/v1/organization/memberships/superuser")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail == "The Superuser account is unavailable; contact the platform operator."
        # No install topology / internals leaked to the tenant Admin.
        assert "found" not in detail
        assert "CLI" not in detail and "bootstrap" not in detail
        assert "/api/" not in detail
    finally:
        await db.delete(ghost)
        await db.commit()


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


# ─── Invite-link verification (Phase 6.5-h, ADR 0018 item 9) ─────────────────


async def _login_email(
    client: AsyncClient, email: str, password: str, *, organization_id: uuid.UUID | None
) -> None:
    """Log in as an arbitrary account by email/password (for freshly
    created members whose role key isn't in the org fixture)."""
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    if organization_id is not None:
        client.headers["X-Organization-Id"] = str(organization_id)
    else:
        client.headers.pop("X-Organization-Id", None)


async def _create_member(
    client: AsyncClient, org_with_members: dict[str, Any], email: str, role: str = "analyst"
) -> dict[str, Any]:
    """Admin creates a new member; returns the create response JSON
    (carries the one-time password + raw invite token)."""
    await _login_as(client, org_with_members, "admin")
    resp = await client.post(
        "/api/v1/organization/users",
        json={"email": email, "display_name": email.split("@")[0], "role": role},
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def test_create_member_starts_unverified_with_invite_token(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    email = f"invitee-{uuid.uuid4().hex[:8]}@test.example"
    body = await _create_member(client, org_with_members, email)
    assert body["verification_status"] == "unverified"
    assert body["invite_token"]  # raw token returned once
    assert body["invite_token_expires_at"] is not None
    assert body["new_password"]  # still a brand-new account

    # Login carries verification_status so the client can route an
    # unverified user straight to /verify (no /chat → /verify hop).
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": body["new_password"]}
    )
    assert login.status_code == 200
    assert login.json()["verification_status"] == "unverified"


async def test_member_list_exposes_status_never_raw_token(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    email = f"invitee-{uuid.uuid4().hex[:8]}@test.example"
    await _create_member(client, org_with_members, email)
    resp = await client.get("/api/v1/organization/users")
    assert resp.status_code == 200
    row = next(m for m in resp.json() if m["email"] == email)
    assert row["verification_status"] == "unverified"
    assert row["invite_token_expires_at"] is not None
    # The raw token (and its hash) must never leak through the list.
    assert "invite_token" not in row
    assert "verification_token_hash" not in row


async def test_regenerate_invite_requires_users_manage(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    email = f"invitee-{uuid.uuid4().hex[:8]}@test.example"
    body = await _create_member(client, org_with_members, email)
    user_id = body["user_id"]
    # A non-admin role lacks USERS_MANAGE.
    await _login_as(client, org_with_members, "analyst")
    denied = await client.post(
        f"/api/v1/organization/users/{user_id}/regenerate-invite-link"
    )
    assert denied.status_code == 403
    # The admin can.
    await _login_as(client, org_with_members, "admin")
    ok = await client.post(f"/api/v1/organization/users/{user_id}/regenerate-invite-link")
    assert ok.status_code == 200, ok.text
    assert ok.json()["invite_token"]
    assert ok.json()["invite_token_expires_at"]


async def test_regenerate_invalidates_old_token(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    org_id = org_with_members["organization_id"]
    email = f"invitee-{uuid.uuid4().hex[:8]}@test.example"
    body = await _create_member(client, org_with_members, email)
    user_id, password, old_token = body["user_id"], body["new_password"], body["invite_token"]

    await _login_as(client, org_with_members, "admin")
    regen = await client.post(f"/api/v1/organization/users/{user_id}/regenerate-invite-link")
    new_token = regen.json()["invite_token"]
    assert new_token != old_token

    await _login_email(client, email, password, organization_id=org_id)
    stale = await client.post("/api/v1/auth/verify-invite", json={"token": old_token})
    assert stale.status_code == 403  # old link no longer valid
    fresh = await client.post("/api/v1/auth/verify-invite", json={"token": new_token})
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["verification_status"] == "verified"


async def test_regenerate_rejected_once_verified(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    org_id = org_with_members["organization_id"]
    email = f"invitee-{uuid.uuid4().hex[:8]}@test.example"
    body = await _create_member(client, org_with_members, email)
    user_id, password, token = body["user_id"], body["new_password"], body["invite_token"]

    await _login_email(client, email, password, organization_id=org_id)
    verified = await client.post("/api/v1/auth/verify-invite", json={"token": token})
    assert verified.status_code == 200

    await _login_as(client, org_with_members, "admin")
    resp = await client.post(f"/api/v1/organization/users/{user_id}/regenerate-invite-link")
    assert resp.status_code == 409


async def test_regenerate_unknown_member_is_404(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    await _login_as(client, org_with_members, "admin")
    resp = await client.post(
        f"/api/v1/organization/users/{uuid.uuid4()}/regenerate-invite-link"
    )
    assert resp.status_code == 404


async def test_verify_invite_success_consumes_token_and_audits(
    client: AsyncClient, db: AsyncSession, org_with_members: dict[str, Any]
) -> None:
    org_id = org_with_members["organization_id"]
    email = f"invitee-{uuid.uuid4().hex[:8]}@test.example"
    body = await _create_member(client, org_with_members, email)
    password, token = body["new_password"], body["invite_token"]

    await _login_email(client, email, password, organization_id=org_id)
    ok = await client.post("/api/v1/auth/verify-invite", json={"token": token})
    assert ok.status_code == 200, ok.text
    assert ok.json()["verification_status"] == "verified"

    # Token is single-use: the hash + expiry are cleared on success.
    db.expire_all()
    user = await db.scalar(select(User).where(User.email == email))
    assert user is not None
    user_id = user.id  # capture before _audit_events' expire_all() detaches it
    assert user.verification_status == "verified"
    assert user.verification_token_hash is None
    assert user.verification_token_expires_at is None

    # A second attempt with the same token is refused (already verified).
    again = await client.post("/api/v1/auth/verify-invite", json={"token": token})
    assert again.status_code == 409

    events = await _audit_events(db, "auth.invite_verification.succeeded")
    assert any(e.user_id == user_id for e in events)


async def test_verify_invite_wrong_token_does_not_consume(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    org_id = org_with_members["organization_id"]
    email = f"invitee-{uuid.uuid4().hex[:8]}@test.example"
    body = await _create_member(client, org_with_members, email)
    password, token = body["new_password"], body["invite_token"]

    await _login_email(client, email, password, organization_id=org_id)
    bad = await client.post("/api/v1/auth/verify-invite", json={"token": "not-the-token"})
    assert bad.status_code == 403
    # The real token still works — a wrong paste didn't burn it.
    good = await client.post("/api/v1/auth/verify-invite", json={"token": token})
    assert good.status_code == 200


async def test_verify_invite_expired_token(
    client: AsyncClient, db: AsyncSession, org_with_members: dict[str, Any]
) -> None:
    org_id = org_with_members["organization_id"]
    email = f"invitee-{uuid.uuid4().hex[:8]}@test.example"
    body = await _create_member(client, org_with_members, email)
    password, token = body["new_password"], body["invite_token"]

    # Backdate the token's expiry.
    user = await db.scalar(select(User).where(User.email == email))
    assert user is not None
    user.verification_token_expires_at = datetime.now(UTC) - timedelta(hours=1)
    await db.commit()

    await _login_email(client, email, password, organization_id=org_id)
    resp = await client.post("/api/v1/auth/verify-invite", json={"token": token})
    assert resp.status_code == 403
    assert "expired" in resp.json()["detail"].lower()

    # Expired path is logged and does NOT consume the token.
    events = await _audit_events(db, "auth.invite_verification.failed")
    assert any(e.event_data.get("reason") == "expired" for e in events)
    db.expire_all()
    user = await db.scalar(select(User).where(User.email == email))
    assert user is not None and user.verification_token_hash is not None


async def test_unverified_member_blocked_but_self_endpoints_reachable(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    org_id = org_with_members["organization_id"]
    email = f"invitee-{uuid.uuid4().hex[:8]}@test.example"
    body = await _create_member(client, org_with_members, email, role="admin")
    password = body["new_password"]

    await _login_email(client, email, password, organization_id=org_id)
    # Org data is gated: chat is in every role's baseline, so a 403 here is
    # the verification gate (a verified admin would get 422 on the empty body).
    gated = await client.post("/api/v1/chat", json={})
    assert gated.status_code == 403
    # Self-service endpoints stay reachable so the user can escape the gate.
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["verification_status"] == "unverified"
    assert (await client.get("/api/v1/auth/me/organizations")).status_code == 200


async def test_verify_invite_clears_the_gate(
    client: AsyncClient, org_with_members: dict[str, Any]
) -> None:
    org_id = org_with_members["organization_id"]
    email = f"invitee-{uuid.uuid4().hex[:8]}@test.example"
    body = await _create_member(client, org_with_members, email, role="admin")
    password, token = body["new_password"], body["invite_token"]

    await _login_email(client, email, password, organization_id=org_id)
    assert (await client.get("/api/v1/organization/users")).status_code == 403  # gated
    unlocked = await client.post("/api/v1/auth/verify-invite", json={"token": token})
    assert unlocked.status_code == 200
    assert (await client.get("/api/v1/organization/users")).status_code == 200  # unlocked


async def test_regenerate_is_cross_org_isolated(
    client: AsyncClient, db: AsyncSession, org_with_members: dict[str, Any]
) -> None:
    # A member created in org-A is invisible to org-B's Admin: regenerating
    # their invite from org-B returns 404 (not a member of *this* org).
    email = f"invitee-{uuid.uuid4().hex[:8]}@test.example"
    body = await _create_member(client, org_with_members, email)
    user_id = body["user_id"]

    other = await _make_org_with_admin(db)
    await _login_email(
        client, other["admin"]["email"], _MEMBER_PASSWORD, organization_id=other["organization_id"]
    )
    resp = await client.post(f"/api/v1/organization/users/{user_id}/regenerate-invite-link")
    assert resp.status_code == 404
