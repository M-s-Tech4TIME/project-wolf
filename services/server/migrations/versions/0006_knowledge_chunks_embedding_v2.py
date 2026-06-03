"""Add embedding_v2 + embedding_v2_model columns for multi-embedding RRF.

Per ADR 0014: hybrid retrieval gains an optional second vector leg so
operators can chain v1.5 (long-context, fast) with v2-moe (entity-
lookup precision, 512-token limit). Both legs fuse via RRF alongside
the FTS leg. When the secondary embedder isn't configured, the column
stays NULL and search() falls back to the existing 2-leg flow —
backward compatible.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSION = 768


def upgrade() -> None:
    op.add_column(
        "knowledge_chunks",
        sa.Column("embedding_v2", Vector(EMBEDDING_DIMENSION), nullable=True),
    )
    op.add_column(
        "knowledge_chunks",
        sa.Column("embedding_v2_model", sa.String(100), nullable=True),
    )
    # HNSW index on the secondary vector column. Cosine ops match the
    # primary embedding column from migration 0004 — both legs use the
    # same distance function so the fused ranking is apples-to-apples.
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_embedding_v2_hnsw "
        "ON knowledge_chunks USING hnsw (embedding_v2 vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_v2_hnsw")
    op.drop_column("knowledge_chunks", "embedding_v2_model")
    op.drop_column("knowledge_chunks", "embedding_v2")
