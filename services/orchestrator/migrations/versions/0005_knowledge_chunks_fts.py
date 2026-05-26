"""Add full-text-search column to knowledge_chunks for hybrid retrieval.

Per doc 06 §Hybrid retrieval (vector + keyword): security queries are
full of exact tokens — rule IDs (5712), CVE numbers, ATT&CK technique
IDs (T1110), exact process names. Pure semantic search is bad at exact
match; hybrid retrieval (BM25-style + vector) noticeably lifts answer
quality.

This migration adds a `content_tsv` STORED generated column populated
from `content` via `to_tsvector('english', ...)`, plus a GIN index on
it for fast `@@ tsquery` lookups. The vector column + HNSW index from
migration 0004 are unchanged.

Postgres' `ts_rank_cd` is TF-IDF-flavored ranking (not true BM25) but
the practical difference for keyword recall is small at our corpus
scale, and using the built-in FTS keeps the deployment dependency-free
(no `pg_search` extension required per ADR 0008's lean-Postgres posture).

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Generated, STORED column — auto-populates from content on insert /
    # update and survives a `pg_dump` cleanly. Backfills existing rows
    # immediately as part of the ALTER (no separate UPDATE needed).
    op.execute(
        "ALTER TABLE knowledge_chunks "
        "ADD COLUMN content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', content)) STORED"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_content_tsv "
        "ON knowledge_chunks USING gin (content_tsv)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_content_tsv")
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS content_tsv")
