"""Add knowledge_chunks table for Phase 3 RAG layer.

Per `docs/06-knowledge-and-rag.md`: stable-knowledge chunks with structured
metadata and a fixed-dimension embedding column. HNSW index on the embedding
for fast cosine-distance retrieval.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSION = 768


def upgrade() -> None:
    # Ensure the pgvector extension is present. ONBOARDING §3.4 already
    # runs this manually; the IF NOT EXISTS makes the migration idempotent
    # for fresh databases too.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_chunks",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSION), nullable=False),
        sa.Column(
            "chunk_metadata",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("embedding_model", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Filter index: most queries narrow by (tenant_id, source_type) before
    # ranking by vector distance.
    op.create_index(
        "ix_knowledge_chunks_tenant_source",
        "knowledge_chunks",
        ["tenant_id", "source_type"],
    )
    op.create_index(
        "ix_knowledge_chunks_tenant_id",
        "knowledge_chunks",
        ["tenant_id"],
    )

    # HNSW index for cosine-distance retrieval. pgvector's HNSW is
    # incremental — inserts add to the graph without rebuild. Memory cost
    # is acceptable at Phase 3 dev scale; revisit at MSSP scale per doc 06.
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_embedding_hnsw "
        "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw")
    op.drop_index("ix_knowledge_chunks_tenant_id", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_tenant_source", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
