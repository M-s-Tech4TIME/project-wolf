"""Alembic environment configuration.

Reads DATABASE_URL from the app settings so the same configuration works
in Docker, local dev, and CI without editing the ini file.
"""

import asyncio
import os
from logging.config import fileConfig

import wolf_server.audit.models  # noqa: F401
import wolf_server.knowledge.models  # noqa: F401
import wolf_server.organization.models  # noqa: F401
import wolf_server.wazuh.models  # noqa: F401
from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Pull in all models so Alembic can discover them for --autogenerate.
from wolf_server.database import Base

config = context.config

# Logging guard (2026-06-12): when wolf-server runs migrations IN-PROCESS
# at startup, alembic.ini's [logging] sections must not touch the app's
# live logging — fileConfig's defaults kill every existing logger and set
# the root level to WARN, which silenced uvicorn + structlog entirely and
# turned an "address already in use" bind failure into an 11-hour silent
# crash-loop. wolf_server.main sets `configure_logger=False` via Config
# attributes (the canonical alembic escape hatch); CLI invocations keep
# alembic.ini's logging as before, minus the logger-killing default.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Override the sqlalchemy.url from environment.
database_url = os.environ.get("DATABASE_URL", "")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generate SQL without a live DB)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# Postgres-specific indexes declared via op.execute() in migrations
# (e.g., HNSW vector indexes, TSVECTOR full-text indexes) can't be
# expressed in standard SQLAlchemy Index() syntax. They're intentionally
# defined only in the migration, not in the model. alembic-check would
# report spurious drift on them unless filtered out.
_INDEXES_DECLARED_IN_MIGRATIONS_ONLY: frozenset[str] = frozenset(
    {
        "ix_knowledge_chunks_embedding_hnsw",  # HNSW pgvector index (0004)
        "ix_knowledge_chunks_embedding_v2_hnsw",  # HNSW pgvector index (later)
        "ix_knowledge_chunks_content_tsv",  # TSVECTOR full-text index
    }
)


def _include_object(
    object_: object,  # SchemaItem; alembic-typed
    name: str | None,
    type_: str,
    reflected: bool,  # noqa: FBT001
    compare_to: object,  # SchemaItem | None
) -> bool:
    """Filter callback: skip indexes that can't be modelled in SQLA."""
    return not (type_ == "index" and name in _INDEXES_DECLARED_IN_MIGRATIONS_ONLY)


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with an async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
