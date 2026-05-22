"""Bootstrap a tenant: create tenant + admin user + Wazuh connection profile.

Idempotent.  Re-running with the same tenant slug updates URLs and re-stashes
credentials in the secrets backend in place; existing user/role bindings are
preserved if the email already exists.

Usage (typical local dev):
  uv run --package wolf-orchestrator python -m app.management.bootstrap_tenant \\
    --tenant-slug acme --tenant-name "Acme Corp" \\
    --admin-email admin@acme.example --admin-password '<...>' \\
    --opensearch-url https://wazuh.example:9200 \\
    --opensearch-username wolf_ro --opensearch-password '<...>' \\
    --server-api-url https://wazuh.example:55000 \\
    --server-api-username wolf_ro --server-api-password '<...>' \\
    --no-verify-tls

The OpenSearch and Server API credentials are stored in the configured
secrets backend.  They are NEVER persisted to the database.
"""

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Import models so SQLAlchemy metadata is fully populated.
import app.audit.models  # noqa: F401
import app.tenancy.models  # noqa: F401
import app.wazuh.models  # noqa: F401
from app.auth.local import hash_password
from app.config import get_settings
from app.database import Base
from app.secrets_factory import get_secrets_backend
from app.tenancy.context import VALID_ROLES
from app.tenancy.models import Tenant, User, UserTenant
from app.wazuh.models import TenantWazuhConfig
from app.wazuh.resolver import opensearch_credential_key, server_api_credential_key

logger = structlog.get_logger(__name__)


async def _ensure_schema(database_url: str) -> None:
    """For SQLite dev DBs, create tables on the fly.  Postgres uses Alembic."""
    if "sqlite" not in database_url:
        return
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _upsert_tenant(db: AsyncSession, slug: str, name: str) -> Tenant:
    existing = await db.scalar(select(Tenant).where(Tenant.slug == slug))
    if existing is not None:
        existing.name = name
        existing.is_active = True
        existing.updated_at = datetime.now(UTC)
        return existing
    tenant = Tenant(id=uuid.uuid4(), name=name, slug=slug, is_active=True,
                    created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    db.add(tenant)
    await db.flush()
    return tenant


async def _upsert_user(db: AsyncSession, email: str, password: str, display_name: str) -> User:
    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None:
        # Refresh the password if provided so the operator can rotate via re-run.
        existing.hashed_password = hash_password(password)
        existing.is_active = True
        existing.updated_at = datetime.now(UTC)
        return existing
    user = User(
        id=uuid.uuid4(), email=email, display_name=display_name,
        hashed_password=hash_password(password),
        is_active=True, is_superuser=False,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()
    return user


async def _upsert_binding(
    db: AsyncSession, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str
) -> None:
    if role not in VALID_ROLES:
        raise SystemExit(f"Invalid role {role!r}; allowed: {sorted(VALID_ROLES)}")
    existing = await db.scalar(
        select(UserTenant).where(
            UserTenant.user_id == user_id, UserTenant.tenant_id == tenant_id
        )
    )
    if existing is not None:
        existing.role = role
        return
    db.add(UserTenant(
        id=uuid.uuid4(), user_id=user_id, tenant_id=tenant_id, role=role,
        created_at=datetime.now(UTC),
    ))
    await db.flush()


async def _upsert_wazuh_config(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    opensearch_url: str,
    opensearch_index_pattern: str,
    opensearch_credential_key: str,  # noqa: A002 — names the column
    server_api_url: str,
    server_api_credential_key: str,  # noqa: A002
    verify_tls: bool,
    inject_tenant_filter: bool,
) -> TenantWazuhConfig:
    existing = await db.scalar(
        select(TenantWazuhConfig).where(TenantWazuhConfig.tenant_id == tenant_id)
    )
    if existing is not None:
        existing.opensearch_url = opensearch_url
        existing.opensearch_index_pattern = opensearch_index_pattern
        existing.opensearch_credential_key = opensearch_credential_key
        existing.server_api_url = server_api_url
        existing.server_api_credential_key = server_api_credential_key
        existing.verify_tls = verify_tls
        existing.inject_tenant_filter = inject_tenant_filter
        existing.updated_at = datetime.now(UTC)
        return existing
    cfg = TenantWazuhConfig(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        opensearch_url=opensearch_url,
        opensearch_index_pattern=opensearch_index_pattern,
        opensearch_credential_key=opensearch_credential_key,
        server_api_url=server_api_url,
        server_api_credential_key=server_api_credential_key,
        verify_tls=verify_tls,
        inject_tenant_filter=inject_tenant_filter,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(cfg)
    await db.flush()
    return cfg


async def bootstrap_tenant(
    *,
    tenant_slug: str,
    tenant_name: str,
    admin_email: str,
    admin_password: str,
    admin_display_name: str,
    role: str,
    opensearch_url: str,
    opensearch_username: str,
    opensearch_password: str,
    opensearch_index_pattern: str,
    server_api_url: str,
    server_api_username: str,
    server_api_password: str,
    verify_tls: bool,
    inject_tenant_filter: bool,
) -> dict[str, Any]:
    settings = get_settings()
    await _ensure_schema(settings.database_url)

    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        tenant = await _upsert_tenant(db, tenant_slug, tenant_name)
        user = await _upsert_user(db, admin_email, admin_password, admin_display_name)
        await _upsert_binding(db, user.id, tenant.id, role)

        os_key = opensearch_credential_key(tenant.id)
        api_key = server_api_credential_key(tenant.id)
        await _upsert_wazuh_config(
            db, tenant.id,
            opensearch_url=opensearch_url,
            opensearch_index_pattern=opensearch_index_pattern,
            opensearch_credential_key=os_key,
            server_api_url=server_api_url,
            server_api_credential_key=api_key,
            verify_tls=verify_tls,
            inject_tenant_filter=inject_tenant_filter,
        )
        await db.commit()
        tenant_id = tenant.id
        user_id = user.id

    secrets = get_secrets_backend(settings)
    await secrets.set(
        opensearch_credential_key(tenant_id),
        json.dumps({"username": opensearch_username, "password": opensearch_password}),
    )
    await secrets.set(
        server_api_credential_key(tenant_id),
        json.dumps({"username": server_api_username, "password": server_api_password}),
    )

    await engine.dispose()

    return {
        "tenant_id": str(tenant_id),
        "tenant_slug": tenant_slug,
        "user_id": str(user_id),
        "user_email": admin_email,
        "verify_tls": verify_tls,
        "inject_tenant_filter": inject_tenant_filter,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--tenant-slug", required=True)
    p.add_argument("--tenant-name", required=True)
    p.add_argument("--admin-email", required=True)
    p.add_argument("--admin-password", required=True)
    p.add_argument("--admin-display-name", default="Tenant Admin")
    p.add_argument("--role", default="admin", choices=sorted(VALID_ROLES))
    p.add_argument("--opensearch-url", required=True)
    p.add_argument("--opensearch-index-pattern", default="wazuh-alerts-*")
    p.add_argument("--opensearch-username", required=True)
    p.add_argument("--opensearch-password", required=True)
    p.add_argument("--server-api-url", required=True)
    p.add_argument("--server-api-username", required=True)
    p.add_argument("--server-api-password", required=True)
    tls = p.add_mutually_exclusive_group()
    tls.add_argument("--verify-tls", dest="verify_tls", action="store_true",
                     help="Validate TLS certificates (default).")
    tls.add_argument("--no-verify-tls", dest="verify_tls", action="store_false",
                     help="Skip TLS validation (self-signed certs).")
    p.set_defaults(verify_tls=True)

    tf = p.add_mutually_exclusive_group()
    tf.add_argument(
        "--inject-tenant-filter",
        dest="inject_tenant_filter",
        action="store_true",
        help=(
            "Inject `term:{tenant_id:<id>}` into every OpenSearch query. "
            "Use only for pooled-index multi-tenant Wazuh setups where every "
            "alert is stamped with tenant_id at ingest."
        ),
    )
    tf.add_argument(
        "--no-inject-tenant-filter",
        dest="inject_tenant_filter",
        action="store_false",
        help=(
            "Do NOT inject the tenant_id filter (default). For "
            "separate-deployment-per-tenant the credential is the "
            "isolation boundary; filtering on a missing field would "
            "silently return zero hits."
        ),
    )
    p.set_defaults(inject_tenant_filter=False)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    result = asyncio.run(bootstrap_tenant(
        tenant_slug=args.tenant_slug,
        tenant_name=args.tenant_name,
        admin_email=args.admin_email,
        admin_password=args.admin_password,
        admin_display_name=args.admin_display_name,
        role=args.role,
        opensearch_url=args.opensearch_url,
        opensearch_index_pattern=args.opensearch_index_pattern,
        opensearch_username=args.opensearch_username,
        opensearch_password=args.opensearch_password,
        server_api_url=args.server_api_url,
        server_api_username=args.server_api_username,
        server_api_password=args.server_api_password,
        verify_tls=args.verify_tls,
        inject_tenant_filter=args.inject_tenant_filter,
    ))
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
