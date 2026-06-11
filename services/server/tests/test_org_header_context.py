"""Tests for Phase 6.5-c-i — header-based org context (ADR 0018 Round 3).

The cookie identifies the USER; the X-Organization-Id header names the
ORGANIZATION per request (per-tab context). Covers:

  - header selects among the user's memberships; per-request switching
    (the two-tabs-two-orgs workflow) with role following the binding
  - the header can never reach beyond memberships (403 non-member; the
    header names, membership grants)
  - malformed header → 400; header absent → JWT-claim fallback
    (transitional) or 401 when the session is org-less
  - capability gates (6.5-b) compose with the header path
  - login three-shape response: superuser redirect / single-membership
    auto-select / N>1 needs_org_selection (cookie issued, org-less);
    zero memberships → 401 with the ADR's contact-your-admin detail;
    inactive orgs excluded from selection
  - /me reflects the header org (per-tab profile chip)
  - select-organization / switch-organization record membership-validated
    audit events
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
from wolf_server.organization.models import Organization, User, UserOrganization

_PASSWORD = "password123"
_WOLF_PASSWORD = "test-wolf-password-32-chars-long!!"
ORG_HEADER = "X-Organization-Id"


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


@pytest_asyncio.fixture
async def multi_org_user(db: AsyncSession) -> dict[str, Any]:
    """One user, member of TWO active orgs with different roles, plus a
    third org they do NOT belong to and a fourth (inactive) they do."""
    suffix = uuid.uuid4().hex[:8]

    user = User(
        id=uuid.uuid4(),
        email=f"multi-{suffix}@test.example",
        display_name="Multi Org",
        hashed_password=hash_password(_PASSWORD),
        is_active=True,
        is_superuser=False,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(user)

    orgs: dict[str, Organization] = {}
    for key, active in (("alpha", True), ("beta", True), ("foreign", True), ("ghost", False)):
        org = Organization(
            id=uuid.uuid4(),
            name=f"{key.title()} {suffix}",
            slug=f"{key}-{suffix}",
            is_active=active,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(org)
        orgs[key] = org

    for key, role in (("alpha", "admin"), ("beta", "analyst"), ("ghost", "admin")):
        db.add(
            UserOrganization(
                id=uuid.uuid4(),
                user_id=user.id,
                organization_id=orgs[key].id,
                role=role,
                created_at=_now(),
            )
        )
    await db.commit()

    return {
        "user_id": user.id,
        "email": user.email,
        "alpha_id": orgs["alpha"].id,
        "alpha_name": orgs["alpha"].name,
        "beta_id": orgs["beta"].id,
        "foreign_id": orgs["foreign"].id,
        "ghost_id": orgs["ghost"].id,
    }


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
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(existing)
        await db.commit()
    return {"user_id": existing.id, "email": existing.email}


async def _login(client: AsyncClient, email: str, password: str = _PASSWORD) -> Any:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


# ─── Header-based org context ────────────────────────────────────────────────


async def test_header_selects_org_and_role_per_request(
    client: AsyncClient, multi_org_user: dict[str, Any]
) -> None:
    """The two-tabs workflow: one session, two orgs, role follows the
    binding of whichever org the header names — per request."""
    resp = await _login(client, multi_org_user["email"])
    assert resp.status_code == 200
    assert resp.json()["needs_org_selection"] is True

    # "Tab 1": alpha — admin there, so the Admin-only surface works.
    resp = await client.get(
        "/api/v1/organization/users", headers={ORG_HEADER: str(multi_org_user["alpha_id"])}
    )
    assert resp.status_code == 200

    # "Tab 2": beta — analyst there, the same surface is refused (403
    # from the 6.5-b capability gate, composed with the header path).
    resp = await client.get(
        "/api/v1/organization/users", headers={ORG_HEADER: str(multi_org_user["beta_id"])}
    )
    assert resp.status_code == 403
    assert "capability" in resp.json()["detail"]


async def test_header_cannot_reach_beyond_memberships(
    client: AsyncClient, multi_org_user: dict[str, Any]
) -> None:
    await _login(client, multi_org_user["email"])

    # An org the user is NOT a member of: the header names, it never grants.
    resp = await client.get(
        "/api/v1/organization/users", headers={ORG_HEADER: str(multi_org_user["foreign_id"])}
    )
    assert resp.status_code == 403
    assert "not a member" in resp.json()["detail"]

    # A nonexistent org id.
    resp = await client.get("/api/v1/organization/users", headers={ORG_HEADER: str(uuid.uuid4())})
    assert resp.status_code == 403


async def test_header_to_inactive_org_refused(
    client: AsyncClient, multi_org_user: dict[str, Any]
) -> None:
    """A membership in a soft-deleted org must not open it."""
    await _login(client, multi_org_user["email"])
    resp = await client.get(
        "/api/v1/organization/users", headers={ORG_HEADER: str(multi_org_user["ghost_id"])}
    )
    assert resp.status_code == 403


async def test_malformed_header_400(client: AsyncClient, multi_org_user: dict[str, Any]) -> None:
    await _login(client, multi_org_user["email"])
    resp = await client.get("/api/v1/organization/users", headers={ORG_HEADER: "not-a-uuid"})
    assert resp.status_code == 400
    assert ORG_HEADER in resp.json()["detail"]


async def test_header_absent_falls_back_to_jwt_claim(
    client: AsyncClient, seed_organization_and_user: dict[str, Any]
) -> None:
    """Transitional (pre-6.5-c-ii dashboard): org-at-login still works."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={
            "email": seed_organization_and_user["user_email"],
            "password": _PASSWORD,
            "organization_id": str(seed_organization_and_user["organization_id"]),
        },
    )
    assert resp.status_code == 200
    # No header on this request — the JWT org claim carries the context.
    resp = await client.post("/api/v1/chat", json={})
    assert resp.status_code not in (400, 401, 403)


async def test_header_absent_and_orgless_session_401(
    client: AsyncClient, multi_org_user: dict[str, Any]
) -> None:
    """needs_org_selection sessions carry no org claim: org-scoped calls
    without the header get a 401 that names the header to send."""
    await _login(client, multi_org_user["email"])
    resp = await client.get("/api/v1/organization/users")
    assert resp.status_code == 401
    assert ORG_HEADER in resp.json()["detail"]


async def test_unauthenticated_with_header_still_401(
    client: AsyncClient, multi_org_user: dict[str, Any]
) -> None:
    resp = await client.get(
        "/api/v1/organization/users", headers={ORG_HEADER: str(multi_org_user["alpha_id"])}
    )
    assert resp.status_code == 401


# ─── Login three-shape response ──────────────────────────────────────────────


async def test_login_superuser_shape(client: AsyncClient, seed_superuser: dict[str, Any]) -> None:
    resp = await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_superuser"] is True
    assert body["redirect"] == "/superuser/dashboard"
    assert body["organization_id"] is None
    assert body["needs_org_selection"] is False
    assert body["memberships"] is None


async def test_login_single_membership_auto_selects(
    client: AsyncClient, seed_organization_and_user: dict[str, Any]
) -> None:
    resp = await _login(client, seed_organization_and_user["user_email"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["needs_org_selection"] is False
    auto = body["auto_selected_organization"]
    assert auto is not None
    assert auto["organization_id"] == str(seed_organization_and_user["organization_id"])
    assert auto["role"] == "analyst"
    # Legacy flat fields mirror the selection (transitional).
    assert body["organization_id"] == auto["organization_id"]
    assert body["role"] == "analyst"


async def test_login_multi_membership_needs_selection(
    client: AsyncClient, multi_org_user: dict[str, Any]
) -> None:
    resp = await _login(client, multi_org_user["email"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["needs_org_selection"] is True
    assert body["organization_id"] is None
    assert body["role"] is None
    listed = {m["organization_id"]: m["role"] for m in body["memberships"]}
    assert listed == {
        str(multi_org_user["alpha_id"]): "admin",
        str(multi_org_user["beta_id"]): "analyst",
    }
    # The inactive org's membership is NOT offered.
    assert str(multi_org_user["ghost_id"]) not in listed
    # The session cookie was issued (auth-only) — authenticated calls work.
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_login_zero_memberships_401_with_admin_hint(
    client: AsyncClient, db: AsyncSession
) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        email=f"orgless-{suffix}@test.example",
        display_name="Orgless",
        hashed_password=hash_password(_PASSWORD),
        is_active=True,
        is_superuser=False,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(user)
    await db.commit()

    resp = await _login(client, user.email)
    assert resp.status_code == 401
    assert "contact your organization admin" in resp.json()["detail"]


# ─── /me honors the header ───────────────────────────────────────────────────


async def test_me_reflects_header_org(client: AsyncClient, multi_org_user: dict[str, Any]) -> None:
    await _login(client, multi_org_user["email"])

    resp = await client.get(
        "/api/v1/auth/me", headers={ORG_HEADER: str(multi_org_user["alpha_id"])}
    )
    assert resp.status_code == 200
    assert resp.json()["organization_id"] == str(multi_org_user["alpha_id"])
    assert resp.json()["role"] == "admin"

    resp = await client.get("/api/v1/auth/me", headers={ORG_HEADER: str(multi_org_user["beta_id"])})
    assert resp.status_code == 200
    assert resp.json()["role"] == "analyst"

    resp = await client.get(
        "/api/v1/auth/me", headers={ORG_HEADER: str(multi_org_user["foreign_id"])}
    )
    assert resp.status_code == 403


# ─── select-organization / switch-organization ───────────────────────────────


async def test_select_and_switch_record_membership_validated_audit(
    client: AsyncClient, db: AsyncSession, multi_org_user: dict[str, Any]
) -> None:
    await _login(client, multi_org_user["email"])

    resp = await client.post(
        "/api/v1/auth/select-organization",
        json={"organization_id": str(multi_org_user["alpha_id"])},
    )
    assert resp.status_code == 200
    assert resp.json()["organization_name"] == multi_org_user["alpha_name"]
    assert resp.json()["role"] == "admin"

    resp = await client.post(
        "/api/v1/auth/switch-organization",
        json={"organization_id": str(multi_org_user["beta_id"])},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "analyst"

    db.expire_all()
    for event_type, org_key in (
        ("auth.organization.selected", "alpha_id"),
        ("auth.organization.switched", "beta_id"),
    ):
        event = await db.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.event_type == event_type,
                AuditEvent.user_id == multi_org_user["user_id"],
            )
            .order_by(AuditEvent.created_at.desc())
        )
        assert event is not None, event_type
        assert event.organization_id == multi_org_user[org_key]


async def test_select_organization_refuses_non_membership(
    client: AsyncClient, multi_org_user: dict[str, Any]
) -> None:
    await _login(client, multi_org_user["email"])
    for org_id in (multi_org_user["foreign_id"], multi_org_user["ghost_id"]):
        resp = await client.post(
            "/api/v1/auth/select-organization", json={"organization_id": str(org_id)}
        )
        assert resp.status_code == 403

    # Unauthenticated.
    client.cookies.delete("wolf_access_token")
    resp = await client.post(
        "/api/v1/auth/select-organization",
        json={"organization_id": str(multi_org_user["alpha_id"])},
    )
    assert resp.status_code == 401
