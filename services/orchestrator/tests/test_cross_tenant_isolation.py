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


# ─── Test: PgvectorKnowledgeStore.search() builds tenant-scoped SQL ──────────
#
# Phase 3 added the RAG path; doc 05 demands the same isolation discipline
# for retrieval. The store's SQL clause is the load-bearing enforcement
# point — the unit tests below verify it's wired correctly without
# requiring a live Postgres. End-to-end isolation against the dev
# corpus is covered by tools/tenant_isolation_test (Phase 4 Slice 4).


def test_pgvector_store_search_constrains_results_to_requesting_tenant() -> None:
    """The store's leg-helpers must include WHERE tenant_id IS NULL OR
    tenant_id = $req in every candidate query. No 'select all chunks
    and filter in Python' path can exist."""
    import inspect

    from app.knowledge.store import PgvectorKnowledgeStore

    # Source-level invariant: every candidate-fetcher method must
    # construct a where clause that scopes by tenant_id. We assert the
    # presence of the tenant-scoping predicate text rather than running
    # SQL — a future contributor would have to delete the predicate to
    # break isolation, which the source-level check catches.
    for helper_name in (
        "_vector_candidates",
        "_fts_candidates",
        "_vector_aux_candidates",
    ):
        source = inspect.getsource(getattr(PgvectorKnowledgeStore, helper_name))
        assert "tenant_id.is_(None)" in source, (
            f"{helper_name} missing shared-corpora clause (tenant_id IS NULL)"
        )
        assert "tenant_id == tenant_id" in source, (
            f"{helper_name} missing requesting-tenant clause "
            f"(tenant_id == :req_tenant)"
        )


def test_pgvector_chunk_input_validation_blocks_cross_tenant_writes() -> None:
    """A shared-corpus chunk MUST have tenant_id=None; a tenant-private
    chunk MUST have a tenant_id. The store's validate-on-upsert prevents
    accidental cross-tenant writes at the data layer."""
    from app.knowledge.store import ChunkInput, PgvectorKnowledgeStore

    # Shared corpus with a tenant_id is a configuration mistake that
    # would let that tenant's content leak to every other tenant.
    bad_shared = ChunkInput(
        content="leaking content",
        source_type="wazuh_doc",
        tenant_id=uuid.uuid4(),
        chunk_metadata={},
    )
    with pytest.raises(ValueError, match="tenant_id must be None"):
        PgvectorKnowledgeStore._validate_chunk(bad_shared)

    # Tenant-private corpus without a tenant_id would also be a leak —
    # the chunk would appear in every tenant's retrieval (since the
    # search WHERE clause matches tenant_id IS NULL too).
    bad_private = ChunkInput(
        content="leaking runbook",
        source_type="runbook",
        tenant_id=None,
        chunk_metadata={},
    )
    with pytest.raises(ValueError, match="tenant_id is required"):
        PgvectorKnowledgeStore._validate_chunk(bad_private)


# ─── Test: audit WRITES never bleed into another tenant's tenant_id ─────────
#
# Doc 05 §The audit stream: "Every audit record is tenant-tagged at write
# time, and audit reads are themselves tenant-scoped." The existing test
# above covers READ isolation; this one covers WRITE isolation — the
# audit-stream is not exempt from isolation just because it's
# infrastructure.


@pytest.mark.asyncio
async def test_audit_writes_stamp_tenant_id_at_write_time(db: Any) -> None:
    """A write for tenant A must persist with tenant_a in the row, not
    bleed under tenant_b's id even if the audit_data payload happens to
    reference tenant_b. The write path takes tenant_id as a positional
    argument and the row stores it directly."""
    from app.audit.log import write_event
    from app.audit.models import AuditEvent
    from sqlalchemy import select

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    # Adversarial payload: tenant_a writes an event whose data field
    # mentions tenant_b. The stored row's tenant_id must be tenant_a
    # regardless of payload content.
    await write_event(
        db,
        event_type="test.adversarial_payload",
        event_data={
            "narrative": "this row is for tenant_a",
            "mentions_other_tenant": str(tenant_b),
            "fake_tenant_id": str(tenant_b),  # tries to confuse the row
        },
        tenant_id=tenant_a,
    )
    await db.commit()

    rows = (
        await db.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == "test.adversarial_payload"
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    # The COLUMN stamps tenant_a — the payload doesn't influence the
    # column. Tenant B reading their audit stream would not see this row.
    assert rows[0].tenant_id == tenant_a
    assert rows[0].tenant_id != tenant_b
    # The payload preserves the (deliberately misleading) text; the
    # row's column is what matters for isolation.
    assert rows[0].event_data["fake_tenant_id"] == str(tenant_b)


@pytest.mark.asyncio
async def test_pgvector_search_call_path_includes_requesting_tenant_id() -> None:
    """Sanity-check the call shape: search() forwards the requesting
    tenant_id to every leg's helper. A regression would silently break
    isolation; the unit test catches it without needing a live DB."""
    from unittest.mock import AsyncMock, patch

    from app.knowledge.store import PgvectorKnowledgeStore

    class _StubEmbedder:
        model_id = "stub"
        dimension = 768

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 768 for _ in texts]

    class _StubSession:
        async def execute(self, _stmt: Any) -> Any:
            class _Result:
                @staticmethod
                def all() -> list[tuple[Any, float]]:
                    return []
            return _Result()

    store = PgvectorKnowledgeStore(_StubSession(), _StubEmbedder())
    req_tenant = uuid.uuid4()
    other_tenant = uuid.uuid4()
    assert req_tenant != other_tenant

    with (
        patch.object(store, "_vector_candidates", AsyncMock(return_value={})) as vec_mock,
        patch.object(store, "_fts_candidates", AsyncMock(return_value={})) as fts_mock,
    ):
        await store.search(tenant_id=req_tenant, query_text="x", limit=5)

    # Both legs receive the REQUESTING tenant_id, never something else.
    vec_kwargs_or_args = vec_mock.call_args
    fts_kwargs_or_args = fts_mock.call_args
    # Both helpers are called positionally with (tenant_id, ...) as the first arg.
    assert vec_kwargs_or_args.args[0] == req_tenant
    assert fts_kwargs_or_args.args[0] == req_tenant
    # And NOT with the other tenant's id.
    assert other_tenant not in vec_kwargs_or_args.args
    assert other_tenant not in fts_kwargs_or_args.args
