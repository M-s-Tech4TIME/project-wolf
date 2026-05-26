"""Tests for the Phase 3 knowledge layer.

Scope (Slice 1): validation rules + tool surface contracts. The pgvector
roundtrip is proven by the alembic migration + seed CLI working end-to-end
against the dev DB; testing pgvector behavior in pytest would require
either a Postgres test fixture or skipping under SQLite, neither of which
buys us much over a real seed run.

Cross-tenant isolation at the store query level is enforced by the SQL
WHERE clause in PgvectorKnowledgeStore.search() and asserted at the
end-to-end level via test_cross_tenant_isolation (extended in a later
slice once the agent-loop call path through query_runbook is exercised
in tests).
"""

import uuid
from typing import Any

import pytest
from app.knowledge.store import (
    ALL_SOURCE_TYPES,
    SHARED_SOURCE_TYPES,
    TENANT_SOURCE_TYPES,
    ChunkInput,
    PgvectorKnowledgeStore,
)
from app.tools.knowledge import QueryRunbookInput, QueryRunbookTool
from pydantic import ValidationError

# ─── ChunkInput validation rules ─────────────────────────────────────────────


def test_shared_chunk_must_have_null_tenant() -> None:
    chunk = ChunkInput(
        content="Wazuh rule 5710 is...",
        source_type="wazuh_doc",
        tenant_id=uuid.uuid4(),  # invalid — shared corpora must have None
        chunk_metadata={},
    )
    with pytest.raises(ValueError, match="must be None"):
        PgvectorKnowledgeStore._validate_chunk(chunk)


def test_tenant_chunk_requires_tenant_id() -> None:
    chunk = ChunkInput(
        content="Acme runbook...",
        source_type="runbook",
        tenant_id=None,  # invalid — tenant-private corpora require a tenant_id
        chunk_metadata={},
    )
    with pytest.raises(ValueError, match="tenant_id is required"):
        PgvectorKnowledgeStore._validate_chunk(chunk)


def test_unknown_source_type_rejected() -> None:
    chunk = ChunkInput(
        content="something",
        source_type="not_a_real_corpus",
        tenant_id=None,
        chunk_metadata={},
    )
    with pytest.raises(ValueError, match="Unknown source_type"):
        PgvectorKnowledgeStore._validate_chunk(chunk)


def test_empty_content_rejected() -> None:
    chunk = ChunkInput(
        content="   ",  # whitespace-only
        source_type="wazuh_doc",
        tenant_id=None,
        chunk_metadata={},
    )
    with pytest.raises(ValueError, match="content cannot be empty"):
        PgvectorKnowledgeStore._validate_chunk(chunk)


def test_shared_corpora_set_is_disjoint_from_tenant_corpora_set() -> None:
    # Sanity: a chunk cannot be classified both shared and tenant-private.
    assert SHARED_SOURCE_TYPES.isdisjoint(TENANT_SOURCE_TYPES)
    assert SHARED_SOURCE_TYPES | TENANT_SOURCE_TYPES == ALL_SOURCE_TYPES


def test_valid_shared_chunk_passes() -> None:
    chunk = ChunkInput(
        content="Wazuh rule 5710 explanation.",
        source_type="wazuh_doc",
        tenant_id=None,
        chunk_metadata={"rule_id": "5710"},
    )
    # Should not raise.
    PgvectorKnowledgeStore._validate_chunk(chunk)


def test_valid_tenant_chunk_passes() -> None:
    chunk = ChunkInput(
        content="Acme runbook step 1.",
        source_type="runbook",
        tenant_id=uuid.uuid4(),
        chunk_metadata={"rule_id": "5712"},
    )
    PgvectorKnowledgeStore._validate_chunk(chunk)


# ─── query_runbook tool surface ─────────────────────────────────────────────


def test_query_runbook_input_requires_non_empty_query() -> None:
    with pytest.raises(ValidationError):
        QueryRunbookInput(query="")


def test_query_runbook_input_clamps_limit() -> None:
    with pytest.raises(ValidationError):
        QueryRunbookInput(query="foo", limit=0)
    with pytest.raises(ValidationError):
        QueryRunbookInput(query="foo", limit=999)


def test_query_runbook_input_accepts_minimal_args() -> None:
    args = QueryRunbookInput(query="what does rule 5710 do")
    assert args.query == "what does rule 5710 do"
    assert args.limit == 5  # default
    assert args.source_types is None
    assert args.rule_id is None


@pytest.mark.asyncio
async def test_query_runbook_raises_when_store_not_configured() -> None:
    """If exec_ctx.knowledge_store is None the tool must fail loud, not silent."""
    from app.guardrails.limits import DEFAULT_LIMITS
    from app.tenancy.context import TenantContext
    from app.tools.base import ToolExecContext

    tool = QueryRunbookTool()
    args = QueryRunbookInput(query="anything")
    exec_ctx = ToolExecContext(
        tenant=TenantContext(
            tenant_id=uuid.uuid4(),
            tenant_slug="test",
            user_id=uuid.uuid4(),
            user_email="t@example.com",
            role="analyst",
            session_id="test-session",
        ),
        limits=DEFAULT_LIMITS,
        opensearch=None,
        server_api=None,
        knowledge_store=None,  # the failure-case under test
    )
    with pytest.raises(RuntimeError, match="knowledge_store is not configured"):
        await tool.run(exec_ctx, args)


@pytest.mark.asyncio
async def test_query_runbook_passes_filters_to_store() -> None:
    """Tool builds metadata_filters from rule_id/technique and forwards to store."""
    from app.guardrails.limits import DEFAULT_LIMITS
    from app.tenancy.context import TenantContext
    from app.tools.base import ToolExecContext

    captured: dict[str, Any] = {}

    class _StubStore:
        async def search(self, **kwargs: Any) -> list[Any]:
            captured.update(kwargs)
            return []

    tool = QueryRunbookTool()
    args = QueryRunbookInput(
        query="brute force",
        source_types=["wazuh_doc", "runbook"],
        rule_id=5712,
        technique="T1110",
        limit=3,
    )
    tenant_id = uuid.uuid4()
    exec_ctx = ToolExecContext(
        tenant=TenantContext(
            tenant_id=tenant_id,
            tenant_slug="acme",
            user_id=uuid.uuid4(),
            user_email="t@example.com",
            role="analyst",
            session_id="test-session-2",
        ),
        limits=DEFAULT_LIMITS,
        opensearch=None,
        server_api=None,
        knowledge_store=_StubStore(),
    )
    result = await tool.run(exec_ctx, args)

    assert captured["tenant_id"] == tenant_id
    assert captured["query_text"] == "brute force"
    assert captured["source_types"] == ["wazuh_doc", "runbook"]
    assert captured["metadata_filters"] == {"rule_id": "5712", "technique": "T1110"}
    assert captured["limit"] == 3
    # Empty hits — citation still populated with result_count=0.
    assert result.citation.result_count == 0
    assert result.hits == []
