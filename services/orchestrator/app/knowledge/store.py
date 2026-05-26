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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.models import KnowledgeChunk

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
    """A chunk returned from a search, with its distance and metadata."""

    id: uuid.UUID
    content: str
    source_type: str
    tenant_id: uuid.UUID | None
    chunk_metadata: dict[str, Any]
    distance: float


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
        [query_vector] = await self._embedder.embed([query_text])
        stmt = select(
            KnowledgeChunk,
            KnowledgeChunk.embedding.cosine_distance(query_vector).label("distance"),
        )
        # Tenant scoping: shared chunks (tenant_id IS NULL) plus this
        # tenant's private chunks. NEVER the union with any other tenant.
        stmt = stmt.where(
            (KnowledgeChunk.tenant_id.is_(None))
            | (KnowledgeChunk.tenant_id == tenant_id)
        )
        if source_types:
            for st in source_types:
                if st not in ALL_SOURCE_TYPES:
                    raise ValueError(f"Unknown source_type: {st!r}")
            stmt = stmt.where(KnowledgeChunk.source_type.in_(list(source_types)))
        if metadata_filters:
            for key, value in metadata_filters.items():
                # JSONB containment — chunk_metadata @> '{"key": "value"}'
                stmt = stmt.where(
                    KnowledgeChunk.chunk_metadata[key].astext == str(value)
                )
        stmt = stmt.order_by("distance").limit(limit)
        result = await self._session.execute(stmt)
        return [
            RetrievedChunk(
                id=chunk.id,
                content=chunk.content,
                source_type=chunk.source_type,
                tenant_id=chunk.tenant_id,
                chunk_metadata=chunk.chunk_metadata,
                distance=float(distance),
            )
            for chunk, distance in result.all()
        ]

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
