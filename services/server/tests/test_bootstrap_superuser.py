"""Tests for Phase 6.5-a — bootstrap Superuser CLI + Superuser API routes.

Covers (per ADR 0018 §"Decision: bootstrap Superuser"):
  - wrapper-only invocation guard (direct python invocation rejected)
  - create-if-absent: Wolf created with a 32-char password printed once
  - idempotency: second run is a no-op, no password re-printed
  - --rotate-password: new credential, audit event, old password dead
  - login as username "Wolf" → org-less session (organization_id None)
  - POST /users/{id}/password-reset — superuser-only, audit-emitted,
    superuser's own account refused (CLI is its recovery path)
  - POST /organizations/{id}/recovery/admin — break-glass force-add,
    refused while an active Admin exists, audit lands in the org log
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from wolf_server.audit.models import AuditEvent
from wolf_server.auth.local import hash_password, verify_password
from wolf_server.bootstrap import superuser as superuser_cli
from wolf_server.bootstrap.superuser import (
    SUPERUSER_EMAIL,
    SUPERUSER_USERNAME,
    ensure_superuser,
    generate_password,
)
from wolf_server.database import Base
from wolf_server.organization.models import User, UserOrganization

# ─── CLI core ────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def cli_db_url(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> str:
    """A file-backed SQLite DB the CLI's own engine can reach.

    The shared test DB is in-memory (per-engine), invisible to the
    engine ensure_superuser() creates from settings — so the CLI tests
    get their own tmp file, with the schema pre-built the same way
    conftest builds it (knowledge_chunks skipped: Postgres-only types).
    """
    url = f"sqlite+aiosqlite:///{tmp_path}/cli.db"
    eng = create_async_engine(url, echo=False)
    tables = [t for t in Base.metadata.sorted_tables if t.name != "knowledge_chunks"]
    async with eng.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    await eng.dispose()

    monkeypatch.setattr(superuser_cli, "get_settings", lambda: SimpleNamespace(database_url=url))

    # Schema is already built; the real _ensure_schema would create_all
    # over the full metadata (incl. knowledge_chunks) on SQLite.
    async def _noop(_: str) -> None:
        return None

    monkeypatch.setattr(superuser_cli, "_ensure_schema", _noop)
    return url


async def _cli_db_rows(url: str) -> tuple[list[User], list[AuditEvent]]:
    eng = create_async_engine(url, echo=False)
    factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        users = list((await db.execute(select(User))).scalars().all())
        events = list((await db.execute(select(AuditEvent))).scalars().all())
    await eng.dispose()
    return users, events


def test_generate_password_is_32_chars_and_unique() -> None:
    pw1, pw2 = generate_password(), generate_password()
    assert len(pw1) >= 32
    assert pw1 != pw2


def test_direct_invocation_without_wrapper_is_rejected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("WOLF_WRAPPER_VERSION", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        superuser_cli.main([])
    assert exc_info.value.code == 2
    assert "shell wrapper" in capsys.readouterr().err


async def test_create_superuser_when_absent(
    cli_db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await ensure_superuser() == 0

    out = capsys.readouterr().out
    assert SUPERUSER_USERNAME in out
    assert "password" in out
    assert "shown ONCE" in out

    users, events = await _cli_db_rows(cli_db_url)
    assert len(users) == 1
    wolf = users[0]
    assert wolf.email == SUPERUSER_EMAIL
    assert wolf.display_name == SUPERUSER_USERNAME
    assert wolf.is_superuser is True
    assert wolf.is_active is True
    assert wolf.hashed_password is not None
    # Phase 6.5-h: the Superuser is created VERIFIED — it never goes
    # through the invite flow, and a granted consent-gate membership must
    # not be locked out by the verification gate (organization/context.py).
    assert wolf.verification_status == "verified"
    assert [e.event_type for e in events] == ["superuser.bootstrap.created"]


async def test_second_run_is_idempotent_no_password_printed(
    cli_db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await ensure_superuser() == 0
    capsys.readouterr()  # discard the create banner

    assert await ensure_superuser() == 0
    out = capsys.readouterr().out
    assert "already exists" in out
    assert "password :" not in out  # the banner's credential line

    users, events = await _cli_db_rows(cli_db_url)
    assert len(users) == 1
    assert len(events) == 1  # no second audit event


async def test_rotate_password_changes_credential_and_audits(
    cli_db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await ensure_superuser() == 0
    capsys.readouterr()

    users, _ = await _cli_db_rows(cli_db_url)
    old_hash = users[0].hashed_password

    assert await ensure_superuser(rotate_password=True) == 0
    out = capsys.readouterr().out
    assert "rotated" in out

    users, events = await _cli_db_rows(cli_db_url)
    assert users[0].hashed_password != old_hash
    assert [e.event_type for e in events] == [
        "superuser.bootstrap.created",
        "superuser.password.rotated",
    ]


async def test_rotate_without_existing_superuser_fails(
    cli_db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await ensure_superuser(rotate_password=True) == 3
    assert "no Superuser exists" in capsys.readouterr().err
    users, _ = await _cli_db_rows(cli_db_url)
    assert users == []


# ─── API: login as Wolf + superuser-only routes ──────────────────────────────

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
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(existing)
        await db.commit()
    return {"user_id": existing.id, "email": existing.email}


async def _login(client: AsyncClient, email: str, password: str) -> Any:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def test_superuser_logs_in_by_username_with_no_org(
    client: AsyncClient, seed_superuser: dict[str, Any]
) -> None:
    resp = await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_superuser"] is True
    assert body["display_name"] == SUPERUSER_USERNAME

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["organization_id"] is None
    assert me.json()["role"] == "superuser"


async def test_superuser_login_wrong_password_rejected(
    client: AsyncClient, seed_superuser: dict[str, Any]
) -> None:
    resp = await _login(client, SUPERUSER_USERNAME, "wrong-password")
    assert resp.status_code == 401


async def test_password_reset_requires_superuser(
    client: AsyncClient, seed_organization_and_user: dict[str, Any]
) -> None:
    # Authenticated as a regular analyst — must be refused.
    resp = await _login(client, seed_organization_and_user["user_email"], "password123")
    assert resp.status_code == 200

    target = seed_organization_and_user["user_id"]
    resp = await client.post(f"/api/v1/users/{target}/password-reset")
    assert resp.status_code == 403


async def test_password_reset_unauthenticated_rejected(client: AsyncClient) -> None:
    resp = await client.post(f"/api/v1/users/{uuid.uuid4()}/password-reset")
    assert resp.status_code == 401


async def test_superuser_resets_user_password_and_audits(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    seed_organization_and_user: dict[str, Any],
) -> None:
    resp = await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)
    assert resp.status_code == 200

    target_id = seed_organization_and_user["user_id"]
    resp = await client.post(f"/api/v1/users/{target_id}/password-reset")
    assert resp.status_code == 200
    new_password = resp.json()["new_password"]
    assert len(new_password) >= 32

    # Old credential dead, new credential live.
    target = await db.scalar(select(User).where(User.id == target_id))
    assert target is not None
    await db.refresh(target)
    assert target.hashed_password is not None
    assert not verify_password("password123", target.hashed_password)
    assert verify_password(new_password, target.hashed_password)

    event = await db.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "superuser.user_password.reset")
        .order_by(AuditEvent.created_at.desc())
    )
    assert event is not None
    assert event.user_id == seed_superuser["user_id"]
    assert event.event_data is not None
    assert event.event_data["target_user_id"] == str(target_id)


async def test_superuser_cannot_reset_own_password_via_api(
    client: AsyncClient, seed_superuser: dict[str, Any]
) -> None:
    resp = await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)
    assert resp.status_code == 200

    resp = await client.post(f"/api/v1/users/{seed_superuser['user_id']}/password-reset")
    assert resp.status_code == 409
    assert "bootstrap_superuser" in resp.json()["detail"]


# ─── Break-glass password reset by email (Phase 6.5-e.2) ─────────────────────


async def test_password_reset_by_email_requires_superuser(
    client: AsyncClient, seed_organization_and_user: dict[str, Any]
) -> None:
    # Authenticated as an ordinary analyst — must be refused.
    resp = await _login(client, seed_organization_and_user["user_email"], "password123")
    assert resp.status_code == 200
    resp = await client.post(
        "/api/v1/users/password-reset-by-email",
        json={"email": seed_organization_and_user["user_email"]},
    )
    assert resp.status_code == 403


async def test_password_reset_by_email_unknown_email_404(
    client: AsyncClient, seed_superuser: dict[str, Any]
) -> None:
    resp = await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)
    assert resp.status_code == 200
    resp = await client.post(
        "/api/v1/users/password-reset-by-email",
        json={"email": f"nobody-{uuid.uuid4().hex[:8]}@test.example"},
    )
    assert resp.status_code == 404


async def test_superuser_resets_password_by_email_and_audits(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    seed_organization_and_user: dict[str, Any],
) -> None:
    resp = await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)
    assert resp.status_code == 200

    target_id = seed_organization_and_user["user_id"]
    target_email = seed_organization_and_user["user_email"]
    resp = await client.post(
        "/api/v1/users/password-reset-by-email", json={"email": target_email}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == str(target_id)
    new_password = body["new_password"]
    assert len(new_password) >= 32

    # Old credential dead, new credential live.
    target = await db.scalar(select(User).where(User.id == target_id))
    assert target is not None
    await db.refresh(target)
    assert not verify_password("password123", target.hashed_password)
    assert verify_password(new_password, target.hashed_password)

    event = await db.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "superuser.user_password.reset")
        .order_by(AuditEvent.created_at.desc())
    )
    assert event is not None
    assert event.event_data is not None
    assert event.event_data["target_user_id"] == str(target_id)
    assert event.event_data["via"] == "email"


async def test_password_reset_by_email_refuses_superuser(
    client: AsyncClient, db: AsyncSession, seed_superuser: dict[str, Any]
) -> None:
    # The bootstrap email "wolf@wolf.local" is a special-use domain that
    # EmailStr rejects outright (422), so exercise the is_superuser GUARD
    # with a superuser account whose email is submittable — the guard,
    # not the address format, must be what refuses the reset.
    su_email = f"super-{uuid.uuid4().hex[:8]}@test.example"
    extra = User(
        id=uuid.uuid4(),
        email=su_email,
        display_name="Extra Superuser",
        hashed_password=hash_password("irrelevant"),
        is_active=True,
        is_superuser=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(extra)
    await db.commit()
    # The DB is session-scoped and shared across tests; a second active
    # Superuser violates the singleton invariant (_get_install_superuser),
    # so remove it in a finally even if an assertion fails.
    try:
        resp = await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)
        assert resp.status_code == 200
        resp = await client.post(
            "/api/v1/users/password-reset-by-email", json={"email": su_email}
        )
        assert resp.status_code == 409
        assert "bootstrap_superuser" in resp.json()["detail"]
    finally:
        await db.delete(extra)
        await db.commit()


async def test_recovery_rejects_empty_display_name(
    client: AsyncClient,
    seed_superuser: dict[str, Any],
    seed_organization_and_user: dict[str, Any],
) -> None:
    # display_name is Field(min_length=1) — an empty value must be a clean
    # 422, not a silently-stored blank name.
    org_id = seed_organization_and_user["organization_id"]
    resp = await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)
    assert resp.status_code == 200
    resp = await client.post(
        f"/api/v1/organizations/{org_id}/recovery/admin",
        json={"email": f"x-{uuid.uuid4().hex[:8]}@test.example", "display_name": ""},
    )
    assert resp.status_code == 422


async def test_recovery_refused_while_active_admin_exists(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    seed_organization_and_user: dict[str, Any],
) -> None:
    org_id = seed_organization_and_user["organization_id"]
    # Give the org an active Admin.
    admin = User(
        id=uuid.uuid4(),
        email=f"admin-{uuid.uuid4().hex[:8]}@test.example",
        display_name="Existing Admin",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_superuser=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(admin)
    db.add(
        UserOrganization(
            id=uuid.uuid4(),
            user_id=admin.id,
            organization_id=org_id,
            role="admin",
            created_at=datetime.now(UTC),
        )
    )
    await db.commit()

    resp = await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)
    assert resp.status_code == 200

    resp = await client.post(
        f"/api/v1/organizations/{org_id}/recovery/admin",
        json={"email": "new-admin@test.example"},
    )
    assert resp.status_code == 409
    assert "active Admin" in resp.json()["detail"]


async def test_recovery_force_adds_admin_to_adminless_org(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    seed_organization_and_user: dict[str, Any],
) -> None:
    # The seeded org has only an analyst — zero Admins.
    org_id = seed_organization_and_user["organization_id"]
    new_admin_email = f"recovered-{uuid.uuid4().hex[:8]}@test.example"

    resp = await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)
    assert resp.status_code == 200

    resp = await client.post(
        f"/api/v1/organizations/{org_id}/recovery/admin",
        json={"email": new_admin_email, "display_name": "Recovered Admin"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["role"] == "admin"
    assert body["new_password"] is not None
    assert len(body["new_password"]) >= 32

    # Binding exists with role=admin.
    binding = await db.scalar(
        select(UserOrganization).where(
            UserOrganization.user_id == uuid.UUID(body["user_id"]),
            UserOrganization.organization_id == org_id,
        )
    )
    assert binding is not None
    assert binding.role == "admin"

    # Audit event is org-scoped (visible in the org's own log) and
    # flags the recovery flow.
    event = await db.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "organization.recovery.admin_added")
        .order_by(AuditEvent.created_at.desc())
    )
    assert event is not None
    assert event.organization_id == org_id
    assert event.user_id == seed_superuser["user_id"]
    assert event.event_data is not None
    assert event.event_data["recovery_flow"] is True
    assert event.event_data["created_new_user"] is True

    # The new Admin can actually log in with the returned password
    # (single membership → auto-select shape).
    resp = await _login(client, new_admin_email, body["new_password"])
    assert resp.status_code == 200
    assert resp.json()["auto_selected_organization"]["role"] == "admin"


async def test_recovery_promotes_existing_member_without_new_password(
    client: AsyncClient,
    db: AsyncSession,
    seed_superuser: dict[str, Any],
    seed_organization_and_user: dict[str, Any],
) -> None:
    # Reuse the seeded analyst's email — they get promoted, not re-created.
    org_id = seed_organization_and_user["organization_id"]
    analyst_email = seed_organization_and_user["user_email"]

    resp = await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)
    assert resp.status_code == 200

    resp = await client.post(
        f"/api/v1/organizations/{org_id}/recovery/admin",
        json={"email": analyst_email},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["user_id"] == str(seed_organization_and_user["user_id"])
    assert body["new_password"] is None

    binding = await db.scalar(
        select(UserOrganization).where(
            UserOrganization.user_id == seed_organization_and_user["user_id"],
            UserOrganization.organization_id == org_id,
        )
    )
    assert binding is not None
    await db.refresh(binding)
    assert binding.role == "admin"


async def test_recovery_unknown_org_404(
    client: AsyncClient, seed_superuser: dict[str, Any]
) -> None:
    resp = await _login(client, SUPERUSER_USERNAME, _WOLF_PASSWORD)
    assert resp.status_code == 200

    resp = await client.post(
        f"/api/v1/organizations/{uuid.uuid4()}/recovery/admin",
        json={"email": "whoever@test.example"},
    )
    assert resp.status_code == 404


async def test_recovery_requires_superuser(
    client: AsyncClient, seed_organization_and_user: dict[str, Any]
) -> None:
    resp = await _login(client, seed_organization_and_user["user_email"], "password123")
    assert resp.status_code == 200

    resp = await client.post(
        f"/api/v1/organizations/{seed_organization_and_user['organization_id']}/recovery/admin",
        json={"email": "x@test.example"},
    )
    assert resp.status_code == 403


async def test_regular_user_login_still_carries_org(
    client: AsyncClient, seed_organization_and_user: dict[str, Any]
) -> None:
    """Regression guard: the Superuser special-case must not change the
    normal login contract — a single-membership org member still gets
    their org via the auto-select shape (ADR 0018 §login UX)."""
    resp = await _login(client, seed_organization_and_user["user_email"], "password123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_superuser"] is False
    auto = body["auto_selected_organization"]
    assert auto["organization_id"] == str(seed_organization_and_user["organization_id"])
    assert auto["role"] == "analyst"
