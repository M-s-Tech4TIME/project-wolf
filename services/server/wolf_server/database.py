"""SQLAlchemy async engine and session factory.

Usage in FastAPI route handlers:
    async def route(db: AsyncSession = Depends(get_db)): ...

The engine is created once at import time from settings.  For tests,
override the DATABASE_URL environment variable before importing this module,
or use the `override_engine` context manager.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from wolf_server.config import get_settings

# Naming convention so SQLAlchemy auto-generates the same constraint
# / index names that our hand-written migrations already use. Without
# this, `alembic check` reports false-positive drift on every named
# constraint (model has unnamed UniqueConstraint, migration has
# "uq_tenants_slug" — both produce the same DB outcome but autogenerate
# sees them as different). Standard alembic recommendation.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Shared declarative base for all SQLAlchemy models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _make_engine(url: str) -> AsyncEngine:
    return create_async_engine(
        url,
        echo=get_settings().is_development,
        pool_pre_ping=True,
        # Connection pool settings suitable for a single-service deployment.
        # Increase pool_size for high-concurrency MSSP deployments.
        pool_size=10,
        max_overflow=20,
    )


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine  # noqa: PLW0603
    if _engine is None:
        _engine = _make_engine(get_settings().database_url)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory  # noqa: PLW0603
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency: yields a database session and closes it after the request."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


@asynccontextmanager
async def db_session() -> AsyncGenerator[AsyncSession]:
    """Context manager for use outside of FastAPI request handlers (e.g. startup tasks)."""
    factory = get_session_factory()
    async with factory() as session:
        yield session
