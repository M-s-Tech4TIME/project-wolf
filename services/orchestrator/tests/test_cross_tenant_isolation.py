"""Cross-tenant isolation tests for Phase 2A read path.

Per doc 05: as Tenant A, attempts to read Tenant B's data must **fail closed**.
These tests cover the negative cases for the read tools/clients delivered in
Phase 2A.  Propose tools and approval reads come in later phases.

Run in CI on every PR (see `tools/tenant_isolation_test/` for the canonical
home; this file is the implementation that the test job runs).
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from app.wazuh.config import WazuhConnection
from app.wazuh.opensearch import WazuhOpenSearchClient
from app.wazuh.query_builder import TenantScopedQueryBuilder
from wolf_common.errors import TenantMismatchError

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _connection_for(tenant_id: uuid.UUID) -> WazuhConnection:
    return WazuhConnection(
        tenant_id=tenant_id,
        opensearch_url="https://os.example.test:9200",
        opensearch_index_pattern="wazuh-alerts-*",
        opensearch_username=f"tenant-{tenant_id}-ro",
        opensearch_password="secret",  # noqa: S106 — test fixture
        server_api_url="https://api.example.test:55000",
        server_api_username=f"tenant-{tenant_id}-api",
        server_api_password="secret",  # noqa: S106 — test fixture
        verify_tls=True,
        inject_tenant_filter=True,
    )


# ─── Test: query builders for different tenants do not produce equal queries ─


def test_two_tenant_builders_with_filter_produce_distinct_queries() -> None:
    """Pinning the multi-tenant pooled-index mode: filter is per-tenant."""
    a = TenantScopedQueryBuilder(uuid.uuid4(), inject_tenant_filter=True)
    b = TenantScopedQueryBuilder(uuid.uuid4(), inject_tenant_filter=True)
    now = datetime.now(UTC)
    qa = a.search_alerts(time_from=now - timedelta(hours=1), time_to=now)
    qb = b.search_alerts(time_from=now - timedelta(hours=1), time_to=now)
    # The two queries differ in the term:{tenant_id} clause.
    assert qa != qb


# ─── Test: tenant-A OpenSearch client rejects a tenant-B-built query ─────────


@pytest.mark.asyncio
async def test_tenant_a_client_rejects_tenant_b_query() -> None:
    """A query built for Tenant B cannot be executed by Tenant A's client."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    builder_b = TenantScopedQueryBuilder(tenant_b)
    now = datetime.now(UTC)
    bad_query = builder_b.search_alerts(
        time_from=now - timedelta(hours=1), time_to=now
    )

    async def _never(_req: httpx.Request) -> httpx.Response:
        raise AssertionError("Tenant A must reject tenant B's query before any HTTP call")

    http = httpx.AsyncClient(
        base_url="https://os.example.test:9200",
        transport=httpx.MockTransport(_never),
        timeout=5.0,
    )
    client = WazuhOpenSearchClient(_connection_for(tenant_a), client=http)
    with pytest.raises(TenantMismatchError, match="tenant_id filter"):
        await client.execute(bad_query)


# ─── Test: tenant-A client rejects a returned doc tagged for tenant B ────────


@pytest.mark.asyncio
async def test_tenant_a_client_rejects_returned_tenant_b_doc() -> None:
    """If a query somehow returns a doc whose source tenant_id is B, fail closed."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    async def _handler(request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = {
            "hits": {
                "total": {"value": 1},
                "hits": [
                    {
                        "_id": "leaked",
                        "_source": {
                            "tenant_id": str(tenant_b),
                            "rule": {"id": "5710"},
                        },
                    }
                ],
            }
        }
        return httpx.Response(200, json=body, request=request)

    http = httpx.AsyncClient(
        base_url="https://os.example.test:9200",
        transport=httpx.MockTransport(_handler),
        timeout=5.0,
    )
    client = WazuhOpenSearchClient(_connection_for(tenant_a), client=http)
    now = datetime.now(UTC)
    good_query = client.query_builder.search_alerts(
        time_from=now - timedelta(hours=1), time_to=now
    )
    with pytest.raises(TenantMismatchError, match="returned doc"):
        await client.execute(good_query)


# ─── Test: audit reads scoped to tenant A do not see tenant B's events ───────


@pytest.mark.asyncio
async def test_audit_reads_are_tenant_scoped(db: Any) -> None:
    """Phase-0 invariant re-verified: audit queries filter by tenant_id."""
    from app.audit.log import write_event
    from app.audit.models import AuditEvent
    from sqlalchemy import select

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    await write_event(
        db,
        event_type="test.cross_tenant.a",
        event_data={"who": "a"},
        tenant_id=tenant_a,
    )
    await write_event(
        db,
        event_type="test.cross_tenant.b",
        event_data={"who": "b"},
        tenant_id=tenant_b,
    )
    await db.commit()

    a_rows = (
        await db.execute(select(AuditEvent).where(AuditEvent.tenant_id == tenant_a))
    ).scalars().all()
    assert all(r.event_data["who"] == "a" for r in a_rows)
    assert all(r.tenant_id == tenant_a for r in a_rows)

    b_rows = (
        await db.execute(select(AuditEvent).where(AuditEvent.tenant_id == tenant_b))
    ).scalars().all()
    assert all(r.event_data["who"] == "b" for r in b_rows)
    assert all(r.tenant_id == tenant_b for r in b_rows)
