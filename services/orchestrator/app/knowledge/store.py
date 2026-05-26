"""KnowledgeStore protocol + pgvector implementation.

Per doc 05 + doc 06: a retrieval call MUST only return chunks visible to
the requesting tenant. The store enforces this at the query level — there
is no `raw_search()` escape hatch. The tenant_id is a required argument on
every read.

Visibility rules:
  - Shared corpora (source_type='wazuh_doc' / 'attack') have tenant_id=NULL
    and are visible to every tenant.
  - Per-tenant corpora (source_type='runbook' / 'past_incident') have a
    non-null tenant_id and are visible ONLY to that tenant.
  - search() returns the union, ranked by vector distance, after the
    metadata filter is applied.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.models import KnowledgeChunk

# Reciprocal Rank Fusion constant — Cormack et al. 2009 found k=60 robust
# across many domains and rerankers. Single tunable knob if we ever need
# to bias toward one ranker; today both legs are unweighted.
RRF_K = 60
# How many candidates each ranker contributes before fusion. Generous
# enough that good chunks ranked mid-tier in one leg can still be
# rescued by the other.
RANKER_CANDIDATE_LIMIT = 25

# Source types Wolf supports today. Validated at write time so unknown
# values can't sneak into the metadata and break retrieval semantics.
SHARED_SOURCE_TYPES = frozenset({"wazuh_doc", "attack"})
TENANT_SOURCE_TYPES = frozenset({"runbook", "past_incident"})
ALL_SOURCE_TYPES = SHARED_SOURCE_TYPES | TENANT_SOURCE_TYPES


@dataclass(frozen=True)
class ChunkInput:
    """A chunk to be embedded and stored. Embedding is computed by the store."""

    content: str
    source_type: str
    # Required for source_type in TENANT_SOURCE_TYPES; must be None for
    # source_type in SHARED_SOURCE_TYPES. Enforced in upsert().
    tenant_id: uuid.UUID | None
    chunk_metadata: dict[str, Any]


@dataclass(frozen=True)
class RetrievedChunk:
    """A chunk returned from a search, with its score and metadata.

    `distance` is the cosine distance from the vector half (lower = more
    similar; in [0, 2]). For pure-vector queries it equals what pgvector's
    `<=>` operator returns. For hybrid queries it's the cosine distance of
    the same chunk's vector against the query embedding — kept on the
    output even when the chunk's RRF rank was driven mostly by the FTS
    leg, so callers can still introspect semantic distance.

    `rrf_score` is the fused Reciprocal Rank Fusion score (higher = more
    relevant); None for pure-vector queries that don't invoke the fusion
    path. The agent loop doesn't currently surface this; it's for tests
    and debug introspection.
    """

    id: uuid.UUID
    content: str
    source_type: str
    tenant_id: uuid.UUID | None
    chunk_metadata: dict[str, Any]
    distance: float
    rrf_score: float | None = None


class KnowledgeStore(Protocol):
    """Vector-store interface for stable-knowledge corpora."""

    async def upsert(self, chunks: Sequence[ChunkInput]) -> list[uuid.UUID]:
        """Embed and persist a batch of chunks. Returns the new chunk IDs."""

    async def search(
        self,
        *,
        tenant_id: uuid.UUID,
        query_text: str,
        source_types: Sequence[str] | None = None,
        metadata_filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[RetrievedChunk]:
        """Hybrid-eventually retrieval. Slice 1 is vector-only."""


class PgvectorKnowledgeStore:
    """Postgres + pgvector implementation of KnowledgeStore."""

    def __init__(self, session: AsyncSession, embedder: Any) -> None:
        # `embedder` is typed Any to avoid a circular import; in practice it
        # implements the EmbeddingProvider protocol.
        self._session = session
        self._embedder = embedder

    async def upsert(self, chunks: Sequence[ChunkInput]) -> list[uuid.UUID]:
        if not chunks:
            return []
        for chunk in chunks:
            self._validate_chunk(chunk)
        vectors = await self._embedder.embed([c.content for c in chunks])
        ids: list[uuid.UUID] = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            row = KnowledgeChunk(
                tenant_id=chunk.tenant_id,
                source_type=chunk.source_type,
                content=chunk.content,
                embedding=vector,
                chunk_metadata=chunk.chunk_metadata,
                embedding_model=self._embedder.model_id,
            )
            self._session.add(row)
            await self._session.flush()
            ids.append(row.id)
        await self._session.commit()
        return ids

    async def search(
        self,
        *,
        tenant_id: uuid.UUID,
        query_text: str,
        source_types: Sequence[str] | None = None,
        metadata_filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[RetrievedChunk]:
        """Hybrid retrieval — vector (cosine) + FTS (ts_rank_cd) fused via RRF.

        Per doc 06 §Hybrid retrieval: security queries are full of exact
        tokens (rule IDs, CVE numbers, ATT&CK technique IDs) where pure
        semantic search underperforms. Hybrid retrieval gives the keyword
        half a fair shot at surfacing the right chunk on those queries
        without losing semantic recall on conceptual ones.

        Algorithm (Reciprocal Rank Fusion, Cormack et al. 2009):
          1. Top-N vector candidates by cosine distance.
          2. Top-N FTS candidates by `ts_rank_cd`.
          3. For each chunk appearing in either, sum 1/(k + rank_in_leg).
          4. Return top-`limit` by fused score.
        """
        if source_types:
            for st in source_types:
                if st not in ALL_SOURCE_TYPES:
                    raise ValueError(f"Unknown source_type: {st!r}")

        [query_vector] = await self._embedder.embed([query_text])

        vector_ranks = await self._vector_candidates(
            tenant_id, query_vector, source_types, metadata_filters
        )
        fts_ranks = await self._fts_candidates(
            tenant_id, query_text, source_types, metadata_filters
        )

        # RRF fusion. A chunk missing from one leg contributes 0 from that
        # leg (no penalty, just no boost). Chunks present in both legs get
        # rewarded proportionally to how high they rank in either.
        rrf: dict[uuid.UUID, float] = {}
        for chunk_id, rank in vector_ranks.items():
            rrf[chunk_id] = rrf.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        for chunk_id, rank in fts_ranks.items():
            rrf[chunk_id] = rrf.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)

        if not rrf:
            return []

        top_ids = sorted(rrf.items(), key=lambda kv: -kv[1])[:limit]
        # Fetch the actual rows for the top IDs, preserving fused order.
        ids_in_order = [chunk_id for chunk_id, _ in top_ids]
        rows_stmt = select(
            KnowledgeChunk,
            KnowledgeChunk.embedding.cosine_distance(query_vector).label("distance"),
        ).where(KnowledgeChunk.id.in_(ids_in_order))
        rows_result = await self._session.execute(rows_stmt)
        by_id = {chunk.id: (chunk, distance) for chunk, distance in rows_result.all()}

        return [
            RetrievedChunk(
                id=chunk_id,
                content=by_id[chunk_id][0].content,
                source_type=by_id[chunk_id][0].source_type,
                tenant_id=by_id[chunk_id][0].tenant_id,
                chunk_metadata=by_id[chunk_id][0].chunk_metadata,
                distance=float(by_id[chunk_id][1]),
                rrf_score=rrf_score,
            )
            for chunk_id, rrf_score in top_ids
            if chunk_id in by_id
        ]

    async def _vector_candidates(
        self,
        tenant_id: uuid.UUID,
        query_vector: list[float],
        source_types: Sequence[str] | None,
        metadata_filters: dict[str, Any] | None,
    ) -> dict[uuid.UUID, int]:
        """Top-N vector candidates with their 1-indexed rank."""
        stmt = (
            select(KnowledgeChunk.id)
            .where(
                (KnowledgeChunk.tenant_id.is_(None))
                | (KnowledgeChunk.tenant_id == tenant_id)
            )
            .order_by(KnowledgeChunk.embedding.cosine_distance(query_vector))
            .limit(RANKER_CANDIDATE_LIMIT)
        )
        stmt = self._apply_metadata_filters(stmt, source_types, metadata_filters)
        result = await self._session.execute(stmt)
        return {chunk_id: rank for rank, (chunk_id,) in enumerate(result.all(), start=1)}

    async def _fts_candidates(
        self,
        tenant_id: uuid.UUID,
        query_text: str,
        source_types: Sequence[str] | None,
        metadata_filters: dict[str, Any] | None,
    ) -> dict[uuid.UUID, int]:
        """Top-N FTS candidates with their 1-indexed rank.

        Uses `plainto_tsquery('english', ...)` so the caller can pass a
        natural-language query without crafting an FTS expression — it
        handles tokenization, stemming, and stop-word removal. Chunks
        with zero match score are excluded by the `@@` predicate; the
        ranker's job is purely to ORDER the ones that do match.
        """
        tsv = KnowledgeChunk.content_tsv
        tsquery = func.plainto_tsquery("english", query_text)
        stmt = (
            select(
                KnowledgeChunk.id,
                func.ts_rank_cd(tsv, tsquery).label("fts_score"),
            )
            .where(
                (KnowledgeChunk.tenant_id.is_(None))
                | (KnowledgeChunk.tenant_id == tenant_id)
            )
            .where(tsv.op("@@")(tsquery))
            .order_by(func.ts_rank_cd(tsv, tsquery).desc())
            .limit(RANKER_CANDIDATE_LIMIT)
        )
        stmt = self._apply_metadata_filters(stmt, source_types, metadata_filters)
        result = await self._session.execute(stmt)
        return {chunk_id: rank for rank, (chunk_id, _) in enumerate(result.all(), start=1)}

    @staticmethod
    def _apply_metadata_filters(
        stmt: Any,
        source_types: Sequence[str] | None,
        metadata_filters: dict[str, Any] | None,
    ) -> Any:
        if source_types:
            stmt = stmt.where(KnowledgeChunk.source_type.in_(list(source_types)))
        if metadata_filters:
            for key, value in metadata_filters.items():
                stmt = stmt.where(
                    KnowledgeChunk.chunk_metadata[key].astext == str(value)
                )
        return stmt

    @staticmethod
    def _validate_chunk(chunk: ChunkInput) -> None:
        if chunk.source_type not in ALL_SOURCE_TYPES:
            raise ValueError(
                f"Unknown source_type {chunk.source_type!r}; expected one of "
                f"{sorted(ALL_SOURCE_TYPES)}"
            )
        if chunk.source_type in SHARED_SOURCE_TYPES and chunk.tenant_id is not None:
            raise ValueError(
                f"source_type={chunk.source_type!r} is shared; tenant_id must "
                f"be None, got {chunk.tenant_id}"
            )
        if chunk.source_type in TENANT_SOURCE_TYPES and chunk.tenant_id is None:
            raise ValueError(
                f"source_type={chunk.source_type!r} is tenant-private; "
                f"tenant_id is required"
            )
        if not chunk.content.strip():
            raise ValueError("Chunk content cannot be empty or whitespace-only")
