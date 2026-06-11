"""Shared pytest fixtures for wolf-server tests.

Uses an in-process SQLite database (via aiosqlite) so tests run without a
real Postgres instance.  CI uses a real Postgres 17 + pgvector service
(see .github/workflows/ci.yml).

Override DATABASE_URL to point at a real Postgres for deeper integration tests.
"""

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Use real Postgres in CI (DATABASE_URL set), else SQLite for local dev.
_DB_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite+aiosqlite:///:memory:",
)

# Patch the database module before importing the app.
os.environ.setdefault("DATABASE_URL", _DB_URL)
os.environ.setdefault("SECRET_KEY", "test-secret-key-exactly-32-chars!!")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRETS_BACKEND", "file")
os.environ.setdefault("SECRETS_FILE_PATH", "/tmp/wolf_test_secrets.enc")  # noqa: S108
os.environ.setdefault("SECRETS_FILE_KEY", "_KRS3GialojQA05LCxS3-JwSds9RBrZ1htT-BDO-I6U=")
# Phase 5.6-c: pin the mTLS settings to paths that will never exist in
# the test environment, so MtlsMiddleware does NOT get mounted on the
# test app. The middleware unit-tests in test_mtls_middleware.py build
# their own app + middleware directly; that's the layer where its
# behaviour is verified. The other tests (auth flow, chat endpoints,
# organization endpoints) hit the real app via TestClient/AsyncClient, which
# can't present a peer cert — without this override they would all
# get 401'd by MtlsMiddleware once `.local/certs/` exist on disk.
os.environ.setdefault("MTLS_CA_PATH", "/nonexistent/wolf-test-no-mtls/ca.pem")


@pytest.fixture(scope="session")
def event_loop() -> Any:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def engine() -> AsyncGenerator[AsyncEngine]:
    """Create the test database engine and schema."""
    import wolf_server.audit.models  # noqa: F401

    # Import all models so Base.metadata is populated.
    import wolf_server.organization.models  # noqa: F401
    import wolf_server.wazuh.models  # noqa: F401
    from wolf_server.database import Base

    eng = create_async_engine(_DB_URL, echo=False)

    if "sqlite" in _DB_URL:
        # Phase 3's knowledge_chunks table uses Postgres-only types
        # (pgvector.Vector + JSONB) — skip it under SQLite. Knowledge-
        # path tests stub the store and don't need the table; tests
        # that need real pgvector run against the dev Postgres DB.
        sqlite_tables = [t for t in Base.metadata.sorted_tables if t.name != "knowledge_chunks"]
        async with eng.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=sqlite_tables))
    else:
        # Postgres: run Alembic migrations.
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        from alembic import command
        from alembic.config import Config

        # Resolve alembic.ini relative to this file, NOT to cwd.
        # pytest can be invoked from repo root (CI) or from
        # services/server/ (dev workflow); the config path must
        # work either way.
        alembic_ini = Path(__file__).resolve().parent.parent / "alembic.ini"
        cfg = Config(str(alembic_ini))
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(1) as pool:
            await loop.run_in_executor(pool, command.upgrade, cfg, "head")

    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """Yield a database session that is rolled back after each test."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(engine: AsyncEngine) -> AsyncGenerator[AsyncClient]:
    """Yield an HTTPX async client wired to the FastAPI app with a test DB."""
    from wolf_server.database import get_db  # noqa: PLC0415
    from wolf_server.main import create_app  # noqa: PLC0415

    app = create_app()

    # Override the DB dependency so the app uses our test session.
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ── Seeding helpers ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def seed_organization_and_user(db: AsyncSession) -> dict[str, Any]:
    """Create an organization + user + user_organization binding with unique values per test."""
    from datetime import UTC, datetime

    from wolf_server.auth.local import hash_password
    from wolf_server.organization.models import Organization, User, UserOrganization

    # Use a unique suffix per fixture call to avoid UNIQUE constraint conflicts
    # when multiple tests share the same session-scoped SQLite in-memory DB.
    suffix = uuid.uuid4().hex[:8]

    organization = Organization(
        id=uuid.uuid4(),
        name=f"Test Corp {suffix}",
        slug=f"test-corp-{suffix}",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(organization)

    user = User(
        id=uuid.uuid4(),
        email=f"analyst-{suffix}@test.example",
        display_name="Test Analyst",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_superuser=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(user)

    binding = UserOrganization(
        id=uuid.uuid4(),
        user_id=user.id,
        organization_id=organization.id,
        role="analyst",
        created_at=datetime.now(UTC),
    )
    db.add(binding)
    await db.commit()

    return {
        "organization_id": organization.id,
        "organization_slug": organization.slug,
        "user_id": user.id,
        "user_email": user.email,
        "role": "analyst",
    }
