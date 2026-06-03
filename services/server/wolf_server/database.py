"""SQLAlchemy async engine and session factory.

Usage in FastAPI route handlers:
    async def route(db: AsyncSession = Depends(get_db)): ...

The engine is created once at import time from settings.  For tests,
override the DATABASE_URL environment variable before importing this module,
or use the `override_engine` context manager.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from wolf_server.config import get_settings


class Base(DeclarativeBase):
    """Shared declarative base for all SQLAlchemy models."""


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
