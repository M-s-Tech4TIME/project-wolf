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


# ─── EmbeddingProvider factory ──────────────────────────────────────────────


# ─── Hybrid retrieval (RRF fusion) ──────────────────────────────────────────


def test_rrf_constants_are_sensible() -> None:
    """Cormack et al. 2009 robust default is k=60; candidates ≥ limit so the
    fusion has room to rescue chunks that ranked mid-tier in one leg."""
    from app.knowledge.store import RANKER_CANDIDATE_LIMIT, RRF_K

    assert RRF_K == 60
    assert RANKER_CANDIDATE_LIMIT >= 10


def test_retrieved_chunk_carries_rrf_score() -> None:
    """rrf_score is the fused score (higher = more relevant); None when
    callers don't go through the fusion path."""
    from app.knowledge.store import RetrievedChunk

    chunk = RetrievedChunk(
        id=uuid.uuid4(),
        content="x",
        source_type="wazuh_doc",
        tenant_id=None,
        chunk_metadata={},
        distance=0.5,
        rrf_score=0.0325,
    )
    assert chunk.rrf_score == 0.0325

    chunk_no_rrf = RetrievedChunk(
        id=uuid.uuid4(),
        content="x",
        source_type="wazuh_doc",
        tenant_id=None,
        chunk_metadata={},
        distance=0.5,
    )
    assert chunk_no_rrf.rrf_score is None


@pytest.mark.asyncio
async def test_rrf_fusion_three_legs_chunk_in_all_wins() -> None:
    """ADR 0014 — when aux embedder is wired, the third leg participates.

    A chunk that ranks well in all three legs (BM25 + primary + aux)
    should beat a chunk that only appears in one or two."""
    from unittest.mock import AsyncMock, patch

    from app.knowledge.store import PgvectorKnowledgeStore

    in_all_three = uuid.uuid4()
    primary_only = uuid.uuid4()
    fts_only = uuid.uuid4()
    aux_only = uuid.uuid4()

    class _Row:
        def __init__(self, chunk_id: uuid.UUID, source_type: str) -> None:
            self.id = chunk_id
            self.content = "test"
            self.source_type = source_type
            self.tenant_id = None
            self.chunk_metadata = {}

    rows = {
        in_all_three: _Row(in_all_three, "attack"),
        primary_only: _Row(primary_only, "wazuh_doc"),
        fts_only: _Row(fts_only, "wazuh_doc"),
        aux_only: _Row(aux_only, "runbook"),
    }

    class _StubEmbedder:
        model_id = "primary"
        dimension = 768

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 768 for _ in texts]

    class _StubEmbedderAux:
        model_id = "aux"
        dimension = 768

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.1] * 768 for _ in texts]

    class _StubSession:
        async def execute(self, stmt: Any) -> Any:
            class _Result:
                @staticmethod
                def all() -> list[tuple[Any, float]]:
                    return [
                        (rows[in_all_three], 0.10),
                        (rows[primary_only], 0.20),
                        (rows[fts_only], 0.50),
                        (rows[aux_only], 0.30),
                    ]
            return _Result()

    store = PgvectorKnowledgeStore(
        _StubSession(),
        _StubEmbedder(),
        embedder_aux=_StubEmbedderAux(),
    )

    with (
        patch.object(
            store,
            "_vector_candidates",
            AsyncMock(return_value={in_all_three: 1, primary_only: 2}),
        ),
        patch.object(
            store,
            "_fts_candidates",
            AsyncMock(return_value={in_all_three: 1, fts_only: 2}),
        ),
        patch.object(
            store,
            "_vector_aux_candidates",
            AsyncMock(return_value={in_all_three: 1, aux_only: 2}),
        ) as aux_mock,
    ):
        results = await store.search(
            tenant_id=uuid.uuid4(),
            query_text="anything",
            limit=10,
        )

    # The chunk present in all three legs ranks above the singletons.
    assert results[0].id == in_all_three
    rest_ids = {r.id for r in results[1:]}
    assert rest_ids == {primary_only, fts_only, aux_only}
    # Score math (RRF_K=60): three-leg chunk = 3/61 ≈ 0.049; singletons = 1/62.
    assert results[0].rrf_score is not None
    assert results[0].rrf_score > results[1].rrf_score * 2  # clearly dominant
    # Aux-leg helper was actually called (not no-op'd).
    aux_mock.assert_called_once()


@pytest.mark.asyncio
async def test_rrf_fusion_skips_aux_leg_when_no_aux_embedder() -> None:
    """Default behaviour (Slice-2A) is preserved when embedder_aux=None."""
    from unittest.mock import AsyncMock, patch

    from app.knowledge.store import PgvectorKnowledgeStore

    chunk_id = uuid.uuid4()

    class _Row:
        def __init__(self) -> None:
            self.id = chunk_id
            self.content = "x"
            self.source_type = "wazuh_doc"
            self.tenant_id = None
            self.chunk_metadata = {}

    class _StubEmbedder:
        model_id = "primary"
        dimension = 768

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 768 for _ in texts]

    class _StubSession:
        async def execute(self, stmt: Any) -> Any:
            class _Result:
                @staticmethod
                def all() -> list[tuple[Any, float]]:
                    return [(_Row(), 0.10)]
            return _Result()

    store = PgvectorKnowledgeStore(_StubSession(), _StubEmbedder())  # no aux
    with (
        patch.object(store, "_vector_candidates", AsyncMock(return_value={chunk_id: 1})),
        patch.object(store, "_fts_candidates", AsyncMock(return_value={})),
        patch.object(store, "_vector_aux_candidates", AsyncMock()) as aux_mock,
    ):
        results = await store.search(
            tenant_id=uuid.uuid4(), query_text="x", limit=5
        )
    assert len(results) == 1
    # Aux leg helper was NOT invoked.
    aux_mock.assert_not_called()


@pytest.mark.asyncio
async def test_rrf_fusion_combines_both_legs_correctly() -> None:
    """Chunks present in both legs get boosted vs chunks present in only one.

    Mocks the two internal helpers so the test exercises the fusion math
    without needing pgvector or a real Postgres. The fusion formula is:
        score = sum(1 / (RRF_K + rank_in_leg))
    so a chunk at rank 1 in both legs scores 2 * (1/(60+1)) = 0.03279...
    A chunk at rank 1 in one leg only scores 1/61 = 0.01639...
    """
    from unittest.mock import AsyncMock, patch

    from app.knowledge.store import PgvectorKnowledgeStore

    in_both = uuid.uuid4()
    vector_only = uuid.uuid4()
    fts_only = uuid.uuid4()

    # Mock the SA row fetch — return synthetic rows matching the IDs.
    class _Row:
        def __init__(self, chunk_id: uuid.UUID, source_type: str) -> None:
            self.id = chunk_id
            self.content = "test"
            self.source_type = source_type
            self.tenant_id = None
            self.chunk_metadata = {}

    rows = {
        in_both: _Row(in_both, "wazuh_doc"),
        vector_only: _Row(vector_only, "attack"),
        fts_only: _Row(fts_only, "runbook"),
    }

    class _StubEmbedder:
        model_id = "stub"
        dimension = 768

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 768 for _ in texts]

    class _StubSession:
        async def execute(self, stmt: Any) -> Any:
            # The store fetches rows for the top-K IDs after fusion.
            # Return all three rows; the store filters by RRF ordering.
            class _Result:
                @staticmethod
                def all() -> list[tuple[Any, float]]:
                    return [
                        (rows[in_both], 0.10),
                        (rows[vector_only], 0.20),
                        (rows[fts_only], 0.50),
                    ]

            return _Result()

    store = PgvectorKnowledgeStore(_StubSession(), _StubEmbedder())

    # Force the two leg helpers to return the rankings under test.
    with (
        patch.object(
            store,
            "_vector_candidates",
            AsyncMock(return_value={in_both: 1, vector_only: 2}),
        ),
        patch.object(
            store,
            "_fts_candidates",
            AsyncMock(return_value={in_both: 1, fts_only: 2}),
        ),
    ):
        results = await store.search(
            tenant_id=uuid.uuid4(),
            query_text="anything",
            limit=10,
        )

    # The chunk present in both legs MUST rank above singletons.
    assert results[0].id == in_both
    assert results[0].rrf_score is not None
    # Singletons: vector_only (rank 2) and fts_only (rank 2) score the same.
    # Both should appear, in some order, after in_both.
    rest_ids = {r.id for r in results[1:]}
    assert rest_ids == {vector_only, fts_only}
    # Score math (RRF_K=60): in_both ≈ 1/61 + 1/61 ≈ 0.0328; singletons ≈ 1/62.
    assert results[0].rrf_score > results[1].rrf_score


def test_factory_returns_ollama_adapter_by_default() -> None:
    from app.config import Settings
    from app.knowledge.embeddings import (
        OllamaEmbeddingAdapter,
        make_embedding_provider,
    )

    settings = Settings(
        embedding_provider="ollama",
        embedding_model="nomic-embed-text",
        embedding_dimension=768,
    )
    provider = make_embedding_provider(settings)
    assert isinstance(provider, OllamaEmbeddingAdapter)
    assert provider.dimension == 768
    assert provider.model_id == "ollama:nomic-embed-text"


def test_factory_rejects_unknown_provider() -> None:
    from app.config import Settings
    from app.knowledge.embeddings import make_embedding_provider

    settings = Settings(embedding_provider="not-a-real-runtime")
    with pytest.raises(ValueError, match="Unknown embedding_provider"):
        make_embedding_provider(settings)


def test_factory_accepts_sentence_transformers_aliases() -> None:
    """The factory accepts the canonical name plus common aliases."""
    from app.config import Settings
    from app.knowledge.embeddings import make_embedding_provider

    # Without the optional extra, this would raise ImportError from inside
    # the adapter constructor — that's the contract; we don't try to
    # instantiate here, just verify the factory routes to the right branch.
    # Since sentence-transformers IS installed in dev, construction
    # succeeds. We assert the dispatch is correct.
    for alias in ("sentence-transformers", "sentence_transformers", "st"):
        settings = Settings(
            embedding_provider=alias,
            embedding_model="BAAI/bge-base-en-v1.5",
        )
        provider = make_embedding_provider(settings)
        assert provider.model_id.startswith("st:")


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
