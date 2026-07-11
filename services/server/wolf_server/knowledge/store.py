"""KnowledgeStore protocol + pgvector implementation.

Per doc 05 + doc 06: a retrieval call MUST only return chunks visible to
the requesting organization. The store enforces this at the query level — there
is no `raw_search()` escape hatch. The organization_id is a required argument on
every read.

Visibility rules:
  - Shared corpora (source_type='wazuh_doc' / 'attack') have organization_id=NULL
    and are visible to every organization.
  - Per-organization corpora (source_type='runbook' / 'past_incident') have a
    non-null organization_id and are visible ONLY to that organization.
  - search() returns the union, ranked by vector distance, after the
    metadata filter is applied.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wolf_server.knowledge.models import KnowledgeChunk

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
ORGANIZATION_SOURCE_TYPES = frozenset({"runbook", "past_incident"})
ALL_SOURCE_TYPES = SHARED_SOURCE_TYPES | ORGANIZATION_SOURCE_TYPES


@dataclass(frozen=True)
class ChunkInput:
    """A chunk to be embedded and stored. Embedding is computed by the store."""

    content: str
    source_type: str
    # Required for source_type in ORGANIZATION_SOURCE_TYPES; must be None for
    # source_type in SHARED_SOURCE_TYPES. Enforced in upsert().
    organization_id: uuid.UUID | None
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
    organization_id: uuid.UUID | None
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
        organization_id: uuid.UUID,
        query_text: str,
        source_types: Sequence[str] | None = None,
        metadata_filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[RetrievedChunk]:
        """Hybrid-eventually retrieval. Slice 1 is vector-only."""


class PgvectorKnowledgeStore:
    """Postgres + pgvector implementation of KnowledgeStore.

    Single-leg mode (default): `embedder_aux=None`. search() runs the
    Slice-2A hybrid (BM25 + primary-vector RRF).

    Chained mode (ADR 0014): `embedder_aux=<provider>`. upsert() writes
    both `embedding` and `embedding_v2`; search() runs 3-way RRF
    (BM25 + primary-vector + secondary-vector). Gracefully tolerates
    chunks with NULL `embedding_v2` (just don't contribute to leg 3).
    """

    def __init__(
        self,
        session: AsyncSession,
        embedder: Any,
        *,
        embedder_aux: Any | None = None,
    ) -> None:
        # `embedder` is typed Any to avoid a circular import; in practice
        # it implements the EmbeddingProvider protocol.
        self._session = session
        self._embedder = embedder
        self._embedder_aux = embedder_aux

    @staticmethod
    async def _embed_query(embedder: Any, query_text: str) -> list[float]:
        """Query-side embedding: prefer the provider's `embed_query` (which
        applies an instruction prefix on asymmetric models); fall back to a
        plain `embed` for embedder objects that only implement the batch
        method (older adapters, test stubs)."""
        embed_query = getattr(embedder, "embed_query", None)
        if embed_query is not None:
            vector: list[float] = await embed_query(query_text)
            return vector
        [fallback] = await embedder.embed([query_text])
        return list(fallback)

    async def upsert(self, chunks: Sequence[ChunkInput]) -> list[uuid.UUID]:
        if not chunks:
            return []
        for chunk in chunks:
            self._validate_chunk(chunk)
        texts = [c.content for c in chunks]
        vectors = await self._embedder.embed(texts)
        # Aux embed: best-effort, per-chunk. If a chunk's content doesn't
        # fit the aux model's context (e.g. v2-moe's 512-token limit on
        # a long ATT&CK technique), we record None for that chunk's aux
        # vector and the search() third-leg silently skips it. The
        # primary leg still has the full-fidelity embedding so coverage
        # is preserved per ADR 0014 §Tradeoffs.
        aux_vectors: list[list[float] | None]
        aux_model_id: str | None = None
        if self._embedder_aux is not None:
            aux_model_id = self._embedder_aux.model_id
            aux_vectors = []
            for text in texts:
                try:
                    aux_vec = (await self._embedder_aux.embed([text]))[0]
                    aux_vectors.append(aux_vec)
                except Exception:
                    # Likely a too-long input the aux model can't handle.
                    # Recorded as None; primary leg still indexes the chunk.
                    aux_vectors.append(None)
        else:
            aux_vectors = [None] * len(texts)

        ids: list[uuid.UUID] = []
        for chunk, vector, aux_vector in zip(chunks, vectors, aux_vectors, strict=True):
            row = KnowledgeChunk(
                organization_id=chunk.organization_id,
                source_type=chunk.source_type,
                content=chunk.content,
                embedding=vector,
                chunk_metadata=chunk.chunk_metadata,
                embedding_model=self._embedder.model_id,
                embedding_v2=aux_vector,
                embedding_v2_model=aux_model_id if aux_vector is not None else None,
            )
            self._session.add(row)
            await self._session.flush()
            ids.append(row.id)
        await self._session.commit()
        return ids

    async def search(
        self,
        *,
        organization_id: uuid.UUID,
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
          1. Top-N primary-vector candidates by cosine distance.
          2. Top-N FTS candidates by `ts_rank_cd`.
          3. (ADR 0014, optional) Top-N secondary-vector candidates by
             cosine distance, using the embedding_v2 column populated by
             the aux embedder.
          4. For each chunk appearing in any leg, sum 1/(k + rank_in_leg).
          5. Return top-`limit` by fused score.
        """
        if source_types:
            for st in source_types:
                if st not in ALL_SOURCE_TYPES:
                    raise ValueError(f"Unknown source_type: {st!r}")

        # Queries embed via embed_query — instruction-aware models (BGE,
        # qwen3-embedding) apply their query prefix there, while passages
        # (upsert) always embed raw. Symmetric models behave identically
        # on both paths.
        query_vector = await self._embed_query(self._embedder, query_text)
        query_vector_aux: list[float] | None = None
        if self._embedder_aux is not None:
            query_vector_aux = await self._embed_query(self._embedder_aux, query_text)

        vector_ranks = await self._vector_candidates(
            organization_id, query_vector, source_types, metadata_filters
        )
        fts_ranks = await self._fts_candidates(
            organization_id, query_text, source_types, metadata_filters
        )
        # Third RRF leg activates only when both an aux embedder is wired
        # AND the embedding_v2 column has been populated. Chunks with NULL
        # embedding_v2 are excluded by the predicate inside the helper.
        vector_aux_ranks: dict[uuid.UUID, int] = {}
        if query_vector_aux is not None:
            vector_aux_ranks = await self._vector_aux_candidates(
                organization_id, query_vector_aux, source_types, metadata_filters
            )

        # RRF fusion. A chunk missing from one leg contributes 0 from that
        # leg (no penalty, just no boost). Chunks present in multiple legs
        # get rewarded proportionally to how high they rank in each.
        rrf: dict[uuid.UUID, float] = {}
        for chunk_id, rank in vector_ranks.items():
            rrf[chunk_id] = rrf.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        for chunk_id, rank in fts_ranks.items():
            rrf[chunk_id] = rrf.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        for chunk_id, rank in vector_aux_ranks.items():
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
                organization_id=by_id[chunk_id][0].organization_id,
                chunk_metadata=by_id[chunk_id][0].chunk_metadata,
                distance=float(by_id[chunk_id][1]),
                rrf_score=rrf_score,
            )
            for chunk_id, rrf_score in top_ids
            if chunk_id in by_id
        ]

    async def _vector_candidates(
        self,
        organization_id: uuid.UUID,
        query_vector: list[float],
        source_types: Sequence[str] | None,
        metadata_filters: dict[str, Any] | None,
    ) -> dict[uuid.UUID, int]:
        """Top-N vector candidates with their 1-indexed rank."""
        stmt = (
            select(KnowledgeChunk.id)
            .where(
                (KnowledgeChunk.organization_id.is_(None))
                | (KnowledgeChunk.organization_id == organization_id)
            )
            .order_by(KnowledgeChunk.embedding.cosine_distance(query_vector))
            .limit(RANKER_CANDIDATE_LIMIT)
        )
        stmt = self._apply_metadata_filters(stmt, source_types, metadata_filters)
        result = await self._session.execute(stmt)
        return {chunk_id: rank for rank, (chunk_id,) in enumerate(result.all(), start=1)}

    async def _vector_aux_candidates(
        self,
        organization_id: uuid.UUID,
        query_vector: list[float],
        source_types: Sequence[str] | None,
        metadata_filters: dict[str, Any] | None,
    ) -> dict[uuid.UUID, int]:
        """Top-N secondary-vector candidates (ADR 0014).

        Skips chunks where embedding_v2 IS NULL — these never made it
        through the aux embedder (e.g. content too long for v2-moe's
        512-token window) and would corrupt cosine ranking with a
        zero-vector. Organization scoping clause is identical to the primary
        vector leg.
        """
        stmt = (
            select(KnowledgeChunk.id)
            .where(KnowledgeChunk.embedding_v2.isnot(None))
            .where(
                (KnowledgeChunk.organization_id.is_(None))
                | (KnowledgeChunk.organization_id == organization_id)
            )
            .order_by(KnowledgeChunk.embedding_v2.cosine_distance(query_vector))
            .limit(RANKER_CANDIDATE_LIMIT)
        )
        stmt = self._apply_metadata_filters(stmt, source_types, metadata_filters)
        result = await self._session.execute(stmt)
        return {chunk_id: rank for rank, (chunk_id,) in enumerate(result.all(), start=1)}

    async def _fts_candidates(
        self,
        organization_id: uuid.UUID,
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
                (KnowledgeChunk.organization_id.is_(None))
                | (KnowledgeChunk.organization_id == organization_id)
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
                stmt = stmt.where(KnowledgeChunk.chunk_metadata[key].astext == str(value))
        return stmt

    @staticmethod
    def _validate_chunk(chunk: ChunkInput) -> None:
        if chunk.source_type not in ALL_SOURCE_TYPES:
            raise ValueError(
                f"Unknown source_type {chunk.source_type!r}; expected one of "
                f"{sorted(ALL_SOURCE_TYPES)}"
            )
        if chunk.source_type in SHARED_SOURCE_TYPES and chunk.organization_id is not None:
            raise ValueError(
                f"source_type={chunk.source_type!r} is shared; organization_id must "
                f"be None, got {chunk.organization_id}"
            )
        if chunk.source_type in ORGANIZATION_SOURCE_TYPES and chunk.organization_id is None:
            raise ValueError(
                f"source_type={chunk.source_type!r} is organization-private; "
                f"organization_id is required"
            )
        if not chunk.content.strip():
            raise ValueError("Chunk content cannot be empty or whitespace-only")
