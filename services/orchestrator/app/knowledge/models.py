"""SQLAlchemy model for the knowledge_chunks table.

Per `docs/06-knowledge-and-rag.md`: every chunk carries structured metadata
that retrieval filters on before ranking. The embedding column dimension is
fixed at construction time and tied to `embedding_model` so a model swap
forces a planned re-embedding rather than silent inconsistency.

Tenant scoping:
  - source_type='runbook' or 'past_incident' chunks carry a non-null
    tenant_id and are partitioned per tenant.
  - source_type='wazuh_doc' or 'attack' chunks carry tenant_id=NULL and
    are visible to every tenant (shared corpora per doc 06).
  - The PgvectorKnowledgeStore enforces this at query time.
"""

import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, DateTime, Index, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# nomic-embed-text returns 768-dim vectors. Locked into the schema; changing
# this requires migration 0005+ plus a full re-embed of every chunk.
EMBEDDING_DIMENSION = 768


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class KnowledgeChunk(Base):
    """One retrievable unit of stable knowledge."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_knowledge_chunks_tenant_source", "tenant_id", "source_type"),
        # HNSW vector index — see migration 0004. Declared there because
        # USING hnsw + opclass syntax isn't directly expressible via SA Index.
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=_uuid
    )
    # NULL for shared corpora (wazuh_doc / attack); non-null for per-tenant
    # corpora (runbook / past_incident). Enforced at the store layer.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIMENSION), nullable=False
    )
    # Free-form metadata for retrieval filtering: rule_id, technique,
    # wazuh_version, attack_version, title, url, etc. Per doc 06, "metadata
    # is half the system."
    chunk_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    # Re-embedding trigger: a swap of embedding_model forces a planned
    # re-embed of every chunk produced by the prior model.
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False)
    # Generated lexical-search column — Postgres populates it from `content`
    # on insert/update. Wolf never writes here directly. Migration 0005
    # defines the GENERATED ALWAYS AS clause; this declaration just tells
    # SQLAlchemy the column exists so hybrid search can reference it.
    content_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', content)", persisted=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeChunk id={self.id} source={self.source_type} "
            f"tenant={self.tenant_id} model={self.embedding_model}>"
        )
