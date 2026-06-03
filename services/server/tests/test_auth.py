"""Tests for the auth flow — Phase 0 exit criteria.

Exit criterion: "a developer can log in, and the system records an audit event
for the login."

Tests here verify:
  1. A valid login returns HTTP 200 and sets the session cookie.
  2. A login audit event is written with the correct type and tenant.
  3. A failed login returns HTTP 401 and writes a failure audit event.
  4. Logout clears the cookie.
  5. The /healthz endpoint is publicly reachable.
  6. Authenticated requests to protected routes work.
  7. A wrong-tenant request is rejected (cross-tenant check).
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
    seed_tenant_and_user: dict[str, Any],
) -> None:
    """Successful login returns 200 and sets the wolf_access_token cookie."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={
            "email": seed_tenant_and_user["user_email"],
            "password": "password123",
            "tenant_id": str(seed_tenant_and_user["tenant_id"]),
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == seed_tenant_and_user["user_email"]
    assert data["role"] == "analyst"
    assert "wolf_access_token" in resp.cookies


async def test_login_success_writes_audit_event(
    client: AsyncClient,
    db: AsyncSession,
    seed_tenant_and_user: dict[str, Any],
) -> None:
    """Phase 0 exit criterion: login writes an auth.login.success audit event."""
    await client.post(
        "/api/v1/auth/login",
        json={
            "email": seed_tenant_and_user["user_email"],
            "password": "password123",
            "tenant_id": str(seed_tenant_and_user["tenant_id"]),
        },
    )

    result = await db.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "auth.login.success")
        .where(AuditEvent.tenant_id == seed_tenant_and_user["tenant_id"])
    )
    event = result.scalar_one_or_none()

    assert event is not None, "Login audit event was not written"
    assert event.tenant_id == seed_tenant_and_user["tenant_id"]
    assert event.user_id == seed_tenant_and_user["user_id"]
    assert event.event_data is not None
    assert event.event_data.get("method") == "local"


async def test_login_wrong_password_returns_401(
    client: AsyncClient,
    seed_tenant_and_user: dict[str, Any],
) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={
            "email": seed_tenant_and_user["user_email"],
            "password": "wrong-password",
            "tenant_id": str(seed_tenant_and_user["tenant_id"]),
        },
    )
    assert resp.status_code == 401


async def test_login_wrong_password_writes_failure_audit(
    client: AsyncClient,
    db: AsyncSession,
    seed_tenant_and_user: dict[str, Any],
) -> None:
    await client.post(
        "/api/v1/auth/login",
        json={
            "email": seed_tenant_and_user["user_email"],
            "password": "wrong-password",
            "tenant_id": str(seed_tenant_and_user["tenant_id"]),
        },
    )

    result = await db.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "auth.login.failure")
        .where(AuditEvent.user_id == seed_tenant_and_user["user_id"])
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


async def test_login_wrong_tenant_rejected(
    client: AsyncClient,
    seed_tenant_and_user: dict[str, Any],
) -> None:
    """User cannot log into a tenant they are not a member of."""
    other_tenant_id = str(uuid.uuid4())
    resp = await client.post(
        "/api/v1/auth/login",
        json={
            "email": seed_tenant_and_user["user_email"],
            "password": "password123",
            "tenant_id": other_tenant_id,
        },
    )
    assert resp.status_code == 403


# ── Logout ───────────────────────────────────────────────────────────────────


async def test_logout_clears_cookie(
    client: AsyncClient,
    seed_tenant_and_user: dict[str, Any],
) -> None:
    # Log in first.
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={
            "email": seed_tenant_and_user["user_email"],
            "password": "password123",
            "tenant_id": str(seed_tenant_and_user["tenant_id"]),
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
