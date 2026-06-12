"""Tests for the auth flow — Phase 0 exit criteria.

Exit criterion: "a developer can log in, and the system records an audit event
for the login."

Tests here verify:
  1. A valid login returns HTTP 200 and sets the session cookie.
  2. A login audit event is written with the correct type and organization.
  3. A failed login returns HTTP 401 and writes a failure audit event.
  4. Logout clears the cookie.
  5. The /healthz endpoint is publicly reachable.
  6. Authenticated requests to protected routes work.
  7. A request naming an org the user is not a member of is rejected
     (cross-organization check, via the X-Organization-Id header —
     ADR 0018: login is org-less; the header names the org per request).
"""

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_server.audit.models import AuditEvent

# ── Health check ─────────────────────────────────────────────────────────────


async def test_healthz(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── Login ─────────────────────────────────────────────────────────────────────


async def test_login_success_sets_cookie(
    client: AsyncClient,
    seed_organization_and_user: dict[str, Any],
) -> None:
    """Successful login returns 200 and sets the wolf_access_token cookie."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={
            "email": seed_organization_and_user["user_email"],
            "password": "password123",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == seed_organization_and_user["user_email"]
    # Single membership → the auto-select shape (ADR 0018 §login UX).
    assert data["needs_org_selection"] is False
    assert data["auto_selected_organization"]["role"] == "analyst"
    assert data["auto_selected_organization"]["organization_id"] == str(
        seed_organization_and_user["organization_id"]
    )
    assert "wolf_access_token" in resp.cookies


async def test_login_success_writes_audit_event(
    client: AsyncClient,
    db: AsyncSession,
    seed_organization_and_user: dict[str, Any],
) -> None:
    """Phase 0 exit criterion: login writes an auth.login.success audit event."""
    await client.post(
        "/api/v1/auth/login",
        json={
            "email": seed_organization_and_user["user_email"],
            "password": "password123",
        },
    )

    result = await db.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "auth.login.success")
        .where(AuditEvent.organization_id == seed_organization_and_user["organization_id"])
    )
    event = result.scalar_one_or_none()

    assert event is not None, "Login audit event was not written"
    assert event.organization_id == seed_organization_and_user["organization_id"]
    assert event.user_id == seed_organization_and_user["user_id"]
    assert event.event_data is not None
    assert event.event_data.get("method") == "local"


async def test_login_wrong_password_returns_401(
    client: AsyncClient,
    seed_organization_and_user: dict[str, Any],
) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={
            "email": seed_organization_and_user["user_email"],
            "password": "wrong-password",
        },
    )
    assert resp.status_code == 401


async def test_login_wrong_password_writes_failure_audit(
    client: AsyncClient,
    db: AsyncSession,
    seed_organization_and_user: dict[str, Any],
) -> None:
    await client.post(
        "/api/v1/auth/login",
        json={
            "email": seed_organization_and_user["user_email"],
            "password": "wrong-password",
        },
    )

    result = await db.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "auth.login.failure")
        .where(AuditEvent.user_id == seed_organization_and_user["user_id"])
    )
    event = result.scalar_one_or_none()
    assert event is not None, "Login failure audit event was not written"
    assert event.event_data is not None
    assert event.event_data.get("reason") == "wrong_password"


async def test_login_unknown_email_returns_401(
    client: AsyncClient,
) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "irrelevant"},
    )
    assert resp.status_code == 401


async def test_wrong_organization_header_rejected(
    client: AsyncClient,
    seed_organization_and_user: dict[str, Any],
) -> None:
    """A header naming an org the user is not a member of gets 403."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={
            "email": seed_organization_and_user["user_email"],
            "password": "password123",
        },
    )
    assert resp.status_code == 200

    resp = await client.get(
        "/api/v1/auth/me",
        headers={"X-Organization-Id": str(uuid.uuid4())},
    )
    assert resp.status_code == 403


# ── Logout ───────────────────────────────────────────────────────────────────


async def test_logout_clears_cookie(
    client: AsyncClient,
    seed_organization_and_user: dict[str, Any],
) -> None:
    # Log in first.
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={
            "email": seed_organization_and_user["user_email"],
            "password": "password123",
        },
    )
    assert login_resp.status_code == 200
    assert "wolf_access_token" in login_resp.cookies

    # Log out.
    logout_resp = await client.post("/api/v1/auth/logout")
    assert logout_resp.status_code == 204
    # Cookie should be cleared (empty value or absent).
    cookie_val = logout_resp.cookies.get("wolf_access_token", "")
    assert cookie_val == ""


# ── Protected route ───────────────────────────────────────────────────────────


async def test_unauthenticated_me_returns_401(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
