"""Smoke-test the resolved Wazuh clients against a real deployment.

Calls list_agents (Server API) and search_alerts (OpenSearch) for a tenant
that has been bootstrapped.  Proves end-to-end:
  - DB row + secrets entries are correct
  - The TLS configuration works
  - The credentials authenticate
  - The orchestrator's clients parse the real responses

Usage:
  uv run --package wolf-orchestrator python -m app.management.smoke_wazuh \\
    --tenant-slug acme
"""

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Import models so metadata is populated before SQLite create_all.
import app.audit.models  # noqa: F401
import app.tenancy.models  # noqa: F401
import app.wazuh.models  # noqa: F401
from app.config import get_settings
from app.database import Base
from app.secrets_factory import get_secrets_backend
from app.tenancy.context import TenantContext
from app.tenancy.models import Tenant
from app.wazuh.opensearch import WazuhOpenSearchClient
from app.wazuh.resolver import get_wazuh_connection
from app.wazuh.server_api import WazuhServerApiClient

logger = structlog.get_logger(__name__)


async def _ensure_schema(database_url: str) -> None:
    if "sqlite" not in database_url:
        return
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def smoke_test(tenant_slug: str, *, hours: int = 24) -> None:
    settings = get_settings()
    await _ensure_schema(settings.database_url)

    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        tenant = await db.scalar(select(Tenant).where(Tenant.slug == tenant_slug))
        if tenant is None:
            raise SystemExit(f"No tenant with slug {tenant_slug!r}; run bootstrap_tenant first")

        # Synthesize a TenantContext — this is a system process, not a logged-in user.
        # The role is "admin" purely so VALID_ROLES check passes; no auth boundary here.
        ctx = TenantContext(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            user_id=uuid.UUID(int=0),
            user_email="smoke-test@orchestrator.local",
            role="admin",
            session_id=f"smoke-{uuid.uuid4().hex[:8]}",
        )

        secrets = get_secrets_backend(settings)
        conn = await get_wazuh_connection(ctx, db, secrets)
        sys.stdout.write(
            f"✓ Resolved Wazuh connection for tenant {tenant.slug!r} "
            f"(verify_tls={conn.verify_tls})\n"
        )

        # ── Server API: list_agents ──────────────────────────────────────
        async with WazuhServerApiClient(conn) as api:
            body = await api.get("/agents", params={"limit": 5})
            data = body.get("data", {})
            items = data.get("affected_items", []) or []
            total = int(data.get("total_affected_items", len(items)))
            sys.stdout.write(f"✓ Server API: {total} agents total (first 5):\n")
            for a in items[:5]:
                sys.stdout.write(
                    f"    id={a.get('id'):>3} status={a.get('status'):<10} "
                    f"name={a.get('name')!r}\n"
                )

        # ── OpenSearch: search_alerts ────────────────────────────────────
        async with WazuhOpenSearchClient(conn) as os_client:
            now = datetime.now(UTC)
            query = os_client.query_builder.search_alerts(
                time_from=now - timedelta(hours=hours), time_to=now, size=5
            )
            body = await os_client.execute(query)
            hits = body.get("hits", {}).get("hits", []) or []
            total_obj = body.get("hits", {}).get("total", {})
            if isinstance(total_obj, dict):
                total = int(total_obj.get("value", len(hits)))
            else:
                total = int(total_obj)
            sys.stdout.write(
                f"✓ OpenSearch: {total} alerts in last {hours}h (showing {len(hits)}):\n"
            )
            for h in hits:
                src = h.get("_source", {})
                sys.stdout.write(
                    f"    {src.get('timestamp')} "
                    f"agent={src.get('agent', {}).get('name')!r} "
                    f"rule_id={src.get('rule', {}).get('id')} "
                    f"level={src.get('rule', {}).get('level')}\n"
                )

    await engine.dispose()
    sys.stdout.write("\n✓ Smoke test passed.\n")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--tenant-slug", required=True)
    p.add_argument("--hours", type=int, default=24, help="Alert time-window in hours (default 24)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    asyncio.run(smoke_test(args.tenant_slug, hours=args.hours))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
