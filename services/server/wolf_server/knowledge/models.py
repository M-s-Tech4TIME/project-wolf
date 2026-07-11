"""SQLAlchemy model for the knowledge_chunks table.

Per `docs/06-knowledge-and-rag.md`: every chunk carries structured metadata
that retrieval filters on before ranking. The embedding column dimension is
fixed at construction time and tied to `embedding_model` so a model swap
forces a planned re-embedding rather than silent inconsistency.

Organization scoping:
  - source_type='runbook' or 'past_incident' chunks carry a non-null
    organization_id and are partitioned per organization.
  - source_type='wazuh_doc' or 'attack' chunks carry organization_id=NULL and
    are visible to every organization (shared corpora per doc 06).
  - The PgvectorKnowledgeStore enforces this at query time.
"""

import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, DateTime, Index, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from wolf_server.config import get_embedding_dimensions
from wolf_server.database import Base

# Vector column widths are SETTINGS-DRIVEN (ADR 0033): EMBEDDING_DIMENSION /
# EMBEDDING_DIMENSION_AUX in .env decide the ORM-declared width, frozen at
# import time (SQLAlchemy DDL is static — a dimension change needs a process
# restart). Read via the NARROW EmbeddingDimensions loader — the full
# Settings would run unrelated validators (SECRET_KEY guard) in contexts
# like CI's alembic-check that have no app secrets. The BASELINE migrations
# (0004/0006) create both columns at 768; any other configured width is
# reconciled by the operator tool
# `python -m wolf_server.management.embedding_schema --apply`, which re-types
# the live columns, re-embeds every chunk, and rebuilds the HNSW indexes.
# Until the tool runs, a mismatch fails loudly (Postgres "expected N
# dimensions") — never silently.
EMBEDDING_DIMENSION = get_embedding_dimensions().embedding_dimension
EMBEDDING_DIMENSION_AUX = get_embedding_dimensions().embedding_dimension_aux or EMBEDDING_DIMENSION


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class KnowledgeChunk(Base):
    """One retrievable unit of stable knowledge."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_knowledge_chunks_organization_source", "organization_id", "source_type"),
        # HNSW vector index — see migration 0004. Declared there because
        # USING hnsw + opclass syntax isn't directly expressible via SA Index.
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    # NULL for shared corpora (wazuh_doc / attack); non-null for per-organization
    # corpora (runbook / past_incident). Enforced at the store layer.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSION), nullable=False)
    # Free-form metadata for retrieval filtering: rule_id, technique,
    # wazuh_version, attack_version, title, url, etc. Per doc 06, "metadata
    # is half the system."
    chunk_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    # Re-embedding trigger: a swap of embedding_model forces a planned
    # re-embed of every chunk produced by the prior model.
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False)
    # Optional secondary embedding for ADR 0014's multi-embedding RRF.
    # When `EMBEDDING_MODEL_AUX` is configured, chunks gain a second
    # vector here (typically a different model — e.g. v1.5 primary +
    # v2-moe aux for complementary retrieval). NULL when the operator
    # hasn't configured a secondary embedder; the store's search() then
    # silently drops to the existing 2-leg flow.
    embedding_v2: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSION_AUX), nullable=True
    )
    embedding_v2_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
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
            f"organization={self.organization_id} model={self.embedding_model}>"
        )
