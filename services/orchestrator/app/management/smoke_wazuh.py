"""Smoke-test the resolved Wazuh clients against a real deployment.

Two modes:

  Default (clients-only) — proves the connection layer works:
    DB row + secrets entries are correct, TLS works, credentials
    authenticate, raw responses parse.  Calls list_agents (Server API)
    and search_alerts (OpenSearch) directly via the client wrappers.

  --all-tools — additionally exercises every registered read tool by
    calling its run() method against the live deployment.  Use this to
    flip a tool from "mock-only" to "live-verified" status in
    docs/PROGRESS.md, or to re-verify a tool after a Wazuh upgrade or
    a tool refactor.  Requires --agent-id and --rule-id so the
    agent-and-rule-shaped tools have something to query.

Usage:
  uv run --package wolf-orchestrator python -m app.management.smoke_wazuh \\
    --tenant-slug acme
  uv run --package wolf-orchestrator python -m app.management.smoke_wazuh \\
    --tenant-slug acme --all-tools --agent-id 000 --rule-id 5402
"""

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from pydantic import BaseModel  # noqa: TC002 — used at runtime in `cases` type
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Import models so metadata is populated before SQLite create_all.
import app.audit.models  # noqa: F401
import app.tenancy.models  # noqa: F401
import app.wazuh.models  # noqa: F401
from app.config import get_settings
from app.database import Base
from app.guardrails.limits import DEFAULT_LIMITS
from app.secrets_factory import get_secrets_backend
from app.tenancy.context import TenantContext
from app.tenancy.models import Tenant
from app.tools.agents import (
    GetAgentDetailInput,
    GetAgentDetailTool,
    ListAgentsInput,
    ListAgentsTool,
)
from app.tools.alerts import (
    AggregateAlertsInput,
    AggregateAlertsTool,
    CountAlertsBySeverityInput,
    CountAlertsBySeverityTool,
    GetAgentAlertHistoryInput,
    GetAgentAlertHistoryTool,
    GetEventTimelineInput,
    GetEventTimelineTool,
    SearchAlertsInput,
    SearchAlertsTool,
)
from app.tools.base import ReadTool, ToolExecContext
from app.tools.cluster import GetClusterHealthInput, GetClusterHealthTool
from app.tools.rules import GetRuleDefinitionInput, GetRuleDefinitionTool
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


async def smoke_all_tools(
    tenant_slug: str,
    *,
    hours: int = 24,
    agent_id: str = "000",
    rule_id: int = 5402,
) -> None:
    """Exercise every registered read tool against the live deployment.

    Calls each tool's run() method directly through a ToolExecContext —
    bypassing the dispatcher (which needs an authenticated session) but
    still going through the full Pydantic input/output validation and
    the real client/HTTP layer.  Use after a Wazuh upgrade or a tool
    refactor to confirm nothing has drifted.
    """
    settings = get_settings()
    await _ensure_schema(settings.database_url)

    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        tenant = await db.scalar(select(Tenant).where(Tenant.slug == tenant_slug))
        if tenant is None:
            raise SystemExit(f"No tenant with slug {tenant_slug!r}; run bootstrap_tenant first")
        ctx = TenantContext(
            tenant_id=tenant.id, tenant_slug=tenant.slug,
            user_id=uuid.UUID(int=0), user_email="smoke-test@orchestrator.local",
            role="admin", session_id=f"smoke-{uuid.uuid4().hex[:8]}",
        )
        secrets = get_secrets_backend(settings)
        conn = await get_wazuh_connection(ctx, db, secrets)
        sys.stdout.write(
            f"✓ Resolved Wazuh connection for tenant {tenant.slug!r} "
            f"(verify_tls={conn.verify_tls})\n\n"
        )

        async with (
            WazuhOpenSearchClient(conn) as os_client,
            WazuhServerApiClient(conn) as api,
        ):
            exec_ctx = ToolExecContext(
                tenant=ctx, limits=DEFAULT_LIMITS,
                opensearch=os_client, server_api=api,
            )
            now = datetime.now(UTC)
            window_from = now - timedelta(hours=hours)

            cases: list[tuple[str, ReadTool, BaseModel]] = [
                ("list_agents", ListAgentsTool(),
                 ListAgentsInput(limit=5)),
                ("get_agent_detail", GetAgentDetailTool(),
                 GetAgentDetailInput(agent_id=agent_id)),
                ("get_cluster_health", GetClusterHealthTool(),
                 GetClusterHealthInput()),
                ("get_rule_definition", GetRuleDefinitionTool(),
                 GetRuleDefinitionInput(rule_id=rule_id)),
                ("search_alerts", SearchAlertsTool(),
                 SearchAlertsInput(time_from=window_from, time_to=now, size=5)),
                ("aggregate_alerts", AggregateAlertsTool(),
                 AggregateAlertsInput(
                     time_from=window_from, time_to=now,
                     group_by="rule.level", size=20,
                 )),
                ("count_alerts_by_severity", CountAlertsBySeverityTool(),
                 CountAlertsBySeverityInput(time_from=window_from, time_to=now)),
                ("get_event_timeline", GetEventTimelineTool(),
                 GetEventTimelineInput(
                     time_from=window_from, time_to=now,
                     agent_id=agent_id, size=5,
                 )),
                ("get_agent_alert_history", GetAgentAlertHistoryTool(),
                 GetAgentAlertHistoryInput(
                     time_from=window_from, time_to=now,
                     agent_id=agent_id, size=5,
                 )),
            ]

            passed = 0
            failed = 0
            for name, tool, args in cases:
                try:
                    out = await tool.run(exec_ctx, args)
                    dumped = out.model_dump(mode="json")
                    summary = _shape_summary(dumped)
                    sys.stdout.write(f"  ✓ {name:<28} {summary}\n")
                    passed += 1
                except Exception as e:  # noqa: BLE001 — smoke test surfaces all failures
                    sys.stdout.write(f"  ✗ {name:<28} {type(e).__name__}: {e}\n")
                    failed += 1

    await engine.dispose()
    sys.stdout.write(f"\n{passed}/{passed + failed} tools verified against real Wazuh.\n")
    if failed:
        raise SystemExit(1)


def _shape_summary(payload: dict[str, Any]) -> str:
    """One-line shape descriptor: result_count + first list/dict size."""
    cite = payload.get("citation", {}) or {}
    rc = cite.get("result_count")
    parts: list[str] = []
    if rc is not None:
        parts.append(f"result_count={rc}")
    for k, v in payload.items():
        if k == "citation" or k == "raw":
            continue
        if isinstance(v, list):
            parts.append(f"{k}.len={len(v)}")
            break
    return " ".join(parts) if parts else "(no payload counts)"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--tenant-slug", required=True)
    p.add_argument("--hours", type=int, default=24, help="Alert time-window in hours (default 24)")
    p.add_argument(
        "--all-tools",
        action="store_true",
        help="Exercise every registered read tool, not just the connection layer.",
    )
    p.add_argument(
        "--agent-id",
        default="000",
        help="Agent ID for agent-shaped tools when --all-tools is set (default '000').",
    )
    p.add_argument(
        "--rule-id",
        type=int,
        default=5402,
        help="Rule ID for get_rule_definition when --all-tools is set (default 5402).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.all_tools:
        asyncio.run(smoke_all_tools(
            args.tenant_slug, hours=args.hours,
            agent_id=args.agent_id, rule_id=args.rule_id,
        ))
    else:
        asyncio.run(smoke_test(args.tenant_slug, hours=args.hours))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
