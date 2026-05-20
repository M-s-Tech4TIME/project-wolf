"""Shared pytest fixtures for orchestrator tests.

Uses an in-process SQLite database (via aiosqlite) so tests run without a
real Postgres instance.  CI uses a real Postgres 17 + pgvector service
(see .github/workflows/ci.yml).

Override DATABASE_URL to point at a real Postgres for deeper integration tests.
"""

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
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
os.environ.setdefault(
    "SECRETS_FILE_KEY", "Y2FrZS1mZXJuZXQta2V5LWZvci10ZXN0aW5nPT0="
)


@pytest.fixture(scope="session")
def event_loop() -> Any:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def engine() -> AsyncGenerator[AsyncEngine]:
    """Create the test database engine and schema."""
    import app.audit.models  # noqa: F401

    # Import all models so Base.metadata is populated.
    import app.tenancy.models  # noqa: F401
    from app.database import Base

    eng = create_async_engine(_DB_URL, echo=False)

    if "sqlite" in _DB_URL:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    else:
        # Postgres: run Alembic migrations.
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        from alembic import command
        from alembic.config import Config

        cfg = Config("alembic.ini")
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
    from app.database import get_db  # noqa: PLC0415
    from app.main import create_app  # noqa: PLC0415

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
async def seed_tenant_and_user(db: AsyncSession) -> dict[str, Any]:
    """Create a tenant, a user, and a user_tenant binding with unique values per test."""
    from datetime import UTC, datetime

    from app.auth.local import hash_password
    from app.tenancy.models import Tenant, User, UserTenant

    # Use a unique suffix per fixture call to avoid UNIQUE constraint conflicts
    # when multiple tests share the same session-scoped SQLite in-memory DB.
    suffix = uuid.uuid4().hex[:8]

    tenant = Tenant(
        id=uuid.uuid4(),
        name=f"Test Corp {suffix}",
        slug=f"test-corp-{suffix}",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(tenant)

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

    binding = UserTenant(
        id=uuid.uuid4(),
        user_id=user.id,
        tenant_id=tenant.id,
        role="analyst",
        created_at=datetime.now(UTC),
    )
    db.add(binding)
    await db.commit()

    return {
        "tenant_id": tenant.id,
        "tenant_slug": tenant.slug,
        "user_id": user.id,
        "user_email": user.email,
        "role": "analyst",
    }
