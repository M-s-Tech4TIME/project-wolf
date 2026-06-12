"""Tests for Phase 6.5-g — session-cookie blacklist (ADR 0018).

Covers:
  - InMemorySessionBlacklist: session revocation, TTL expiry, user
    watermark semantics (issued-before revoked / issued-after valid)
  - RedisSessionBlacklist: key shapes, EX TTLs, mget-driven is_revoked
    (stub client — no Redis server involved)
  - factory backend selection by settings.redis_url
  - middleware: a revoked session gets 401 on the next request even
    though the JWT is still signature- and expiry-valid
  - trigger sites: logout revokes the current session; Superuser
    password-reset and force-revoke blacklist ALL the target's sessions
  - re-login after revocation yields a working session (watermark only
    kills tokens issued before it)
"""

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_server.audit.models import AuditEvent
from wolf_server.auth import blacklist as blacklist_module
from wolf_server.auth.blacklist import (
    InMemorySessionBlacklist,
    RedisSessionBlacklist,
    get_session_blacklist,
    reset_session_blacklist,
)
from wolf_server.auth.local import hash_password
from wolf_server.bootstrap.superuser import SUPERUSER_EMAIL, SUPERUSER_USERNAME
from wolf_server.organization.models import User

_WOLF_PASSWORD = "test-wolf-password-32-chars-long!!"
COOKIE = "wolf_access_token"


# ─── InMemorySessionBlacklist unit tests ─────────────────────────────────────


class _Clock:
    """Deterministic, manually-advanced stand-in for both clocks."""

    def __init__(self) -> None:
        self.wall = 1_000_000.0
        self.mono = 50_000.0

    def advance(self, seconds: float) -> None:
        self.wall += seconds
        self.mono += seconds


def _make_inmemory() -> tuple[InMemorySessionBlacklist, _Clock]:
    clock = _Clock()
    bl = InMemorySessionBlacklist(wall_clock=lambda: clock.wall, monotonic_clock=lambda: clock.mono)
    return bl, clock


async def test_inmemory_session_revocation() -> None:
    bl, _ = _make_inmemory()
    await bl.revoke_session("sid-1", ttl_seconds=60)
    assert await bl.is_revoked("sid-1", "user-a", issued_at=999.0)
    assert not await bl.is_revoked("sid-2", "user-a", issued_at=999.0)


async def test_inmemory_session_entry_expires_with_ttl() -> None:
    bl, clock = _make_inmemory()
    await bl.revoke_session("sid-1", ttl_seconds=60)
    clock.advance(59)
    assert await bl.is_revoked("sid-1", "user-a", issued_at=0.0)
    clock.advance(2)  # past the TTL — the token it covered has expired too
    assert not await bl.is_revoked("sid-1", "user-a", issued_at=0.0)


async def test_inmemory_user_watermark_kills_old_tokens_only() -> None:
    bl, clock = _make_inmemory()
    issued_before = clock.wall - 10
    await bl.revoke_user("user-a", ttl_seconds=3600)
    issued_after = clock.wall + 5

    # Token issued before the watermark: revoked (any session_id).
    assert await bl.is_revoked("any-sid", "user-a", issued_at=issued_before)
    # Token issued after (re-login): valid.
    assert not await bl.is_revoked("new-sid", "user-a", issued_at=issued_after)
    # Other users untouched.
    assert not await bl.is_revoked("any-sid", "user-b", issued_at=issued_before)


async def test_inmemory_user_watermark_expires_with_ttl() -> None:
    bl, clock = _make_inmemory()
    issued_before = clock.wall - 10
    await bl.revoke_user("user-a", ttl_seconds=60)
    clock.advance(61)
    assert not await bl.is_revoked("any-sid", "user-a", issued_at=issued_before)


# ─── RedisSessionBlacklist unit tests (stub client) ──────────────────────────


class _StubRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, int]] = {}

    async def set(self, key: str, value: str, ex: int) -> None:
        self.store[key] = (value, ex)

    async def mget(self, *keys: str) -> list[str | None]:
        return [self.store[k][0] if k in self.store else None for k in keys]


@pytest.fixture
def redis_blacklist() -> tuple[RedisSessionBlacklist, _StubRedis]:
    bl = RedisSessionBlacklist("redis://localhost:6399/0")  # lazy client, no connection
    stub = _StubRedis()
    bl._redis = stub  # type: ignore[assignment]
    return bl, stub


async def test_redis_session_revocation_key_and_ttl(
    redis_blacklist: tuple[RedisSessionBlacklist, _StubRedis],
) -> None:
    bl, stub = redis_blacklist
    await bl.revoke_session("sid-9", ttl_seconds=123)
    assert stub.store["wolf:session-blacklist:session:sid-9"] == ("1", 123)
    assert await bl.is_revoked("sid-9", "user-x", issued_at=0.0)
    assert not await bl.is_revoked("sid-other", "user-x", issued_at=0.0)


async def test_redis_user_watermark(
    redis_blacklist: tuple[RedisSessionBlacklist, _StubRedis],
) -> None:
    bl, stub = redis_blacklist
    before = time.time() - 10
    await bl.revoke_user("user-x", ttl_seconds=3600)
    key = "wolf:session-blacklist:user:user-x"
    assert stub.store[key][1] == 3600
    after = float(stub.store[key][0]) + 5

    assert await bl.is_revoked("any-sid", "user-x", issued_at=before)
    assert not await bl.is_revoked("any-sid", "user-x", issued_at=after)
    assert not await bl.is_revoked("any-sid", "user-y", issued_at=before)


# ─── Factory backend selection ───────────────────────────────────────────────


def test_factory_selects_backend_by_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    reset_session_blacklist()
    monkeypatch.setattr(blacklist_module, "get_settings", lambda: SimpleNamespace(redis_url=""))
    assert isinstance(get_session_blacklist(), InMemorySessionBlacklist)

    reset_session_blacklist()
    monkeypatch.setattr(
        blacklist_module,
        "get_settings",
        lambda: SimpleNamespace(redis_url="redis://localhost:6399/0"),
    )
    assert isinstance(get_session_blacklist(), RedisSessionBlacklist)

    reset_session_blacklist()  # later tests re-select from real settings


def test_factory_returns_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    reset_session_blacklist()
    monkeypatch.setattr(blacklist_module, "get_settings", lambda: SimpleNamespace(redis_url=""))
    assert get_session_blacklist() is get_session_blacklist()
    reset_session_blacklist()


# ─── API flows (login → revoke → 401) ────────────────────────────────────────


@pytest_asyncio.fixture
async def seed_superuser(db: AsyncSession) -> dict[str, Any]:
    """Insert the bootstrap Superuser into the shared test DB (idempotent)."""
    existing = await db.scalar(select(User).where(User.email == SUPERUSER_EMAIL))
    if existing is None:
        now = datetime.now(UTC)
        existing = User(
            id=uuid.uuid4(),
            email=SUPERUSER_EMAIL,
            display_name=SUPERUSER_USERNAME,
            hashed_password=hash_password(_WOLF_PASSWORD),
            is_active=True,
            is_superuser=True,
            created_at=now,
            updated_at=now,
        )
        db.add(existing)
        await db.commit()
    return {"user_id": existing.id, "email": existing.email}


async def _login(client: AsyncClient, email: str, password: str) -> str:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    token = client.cookies.get(COOKIE)
    assert token
    return token


async def test_logout_blacklists_the_session_server_side(
    client: AsyncClient, seed_organization_and_user: dict[str, Any]
) -> None:
    """The 6.5-g point: after logout the OLD cookie value must be dead even
    if replayed — cookie deletion alone never invalidated the JWT."""
    token = await _login(
        client,
        seed_organization_and_user["user_email"],
        "password123",
    )

    assert (await client.get("/api/v1/auth/me")).status_code == 200
    assert (await client.post("/api/v1/auth/logout")).status_code == 204

    # Replay the captured cookie — an attacker (or stale tab) holding a copy.
    client.cookies.set(COOKIE, token)
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert "revoked" in resp.json()["detail"].lower()


async def test_password_reset_revokes_all_target_sessions(
    client: AsyncClient,
    db: AsyncSession,
    seed_organization_and_user: dict[str, Any],
    seed_superuser: dict[str, Any],
) -> None:
    analyst_token = await _login(
        client,
        seed_organization_and_user["user_email"],
        "password123",
    )

    await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)
    target_id = seed_organization_and_user["user_id"]
    resp = await client.post(f"/api/v1/users/{target_id}/password-reset")
    assert resp.status_code == 200, resp.text
    new_password = resp.json()["new_password"]

    # The analyst's pre-reset session is dead.
    client.cookies.set(COOKIE, analyst_token)
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert "revoked" in resp.json()["detail"].lower()

    # Re-login with the new credential works (watermark kills only tokens
    # issued BEFORE the reset; iat has 1s granularity, hence the wait).
    await asyncio.sleep(1.1)
    client.cookies.delete(COOKIE)
    await _login(
        client,
        seed_organization_and_user["user_email"],
        new_password,
    )
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_force_revoke_endpoint_requires_superuser(
    client: AsyncClient, seed_organization_and_user: dict[str, Any]
) -> None:
    target = seed_organization_and_user["user_id"]
    # Unauthenticated.
    assert (await client.post(f"/api/v1/users/{target}/sessions/revoke")).status_code == 401

    # Regular member.
    await _login(
        client,
        seed_organization_and_user["user_email"],
        "password123",
    )
    assert (await client.post(f"/api/v1/users/{target}/sessions/revoke")).status_code == 403


async def test_force_revoke_unknown_user_404(
    client: AsyncClient, seed_superuser: dict[str, Any]
) -> None:
    await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)
    resp = await client.post(f"/api/v1/users/{uuid.uuid4()}/sessions/revoke")
    assert resp.status_code == 404


async def test_force_revoke_kills_target_sessions_and_audits(
    client: AsyncClient,
    db: AsyncSession,
    seed_organization_and_user: dict[str, Any],
    seed_superuser: dict[str, Any],
) -> None:
    analyst_token = await _login(
        client,
        seed_organization_and_user["user_email"],
        "password123",
    )

    await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)
    target_id = seed_organization_and_user["user_id"]
    resp = await client.post(f"/api/v1/users/{target_id}/sessions/revoke")
    assert resp.status_code == 204

    client.cookies.set(COOKIE, analyst_token)
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert "revoked" in resp.json()["detail"].lower()

    db.expire_all()
    event = await db.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "superuser.user_sessions.revoked")
        .order_by(AuditEvent.created_at.desc())
    )
    assert event is not None
    assert event.user_id == seed_superuser["user_id"]
    assert event.event_data is not None
    assert event.event_data["target_user_id"] == str(target_id)

    # The credential itself is untouched — force-revoke only forces
    # re-authentication.
    await asyncio.sleep(1.1)
    client.cookies.delete(COOKIE)
    await _login(
        client,
        seed_organization_and_user["user_email"],
        "password123",
    )
    assert (await client.get("/api/v1/auth/me")).status_code == 200
