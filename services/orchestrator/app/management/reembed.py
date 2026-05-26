"""Re-embed chunks whose embedding_model no longer matches the active provider.

Per ADR 0012 §"A model swap is NOT free": flipping EMBEDDING_PROVIDER /
EMBEDDING_MODEL without re-embedding silently degrades retrieval — query
vectors come from the new model, stored vectors come from the old one,
and cosine distance becomes meaningless across the gap.

This CLI is the principled fix. It scans `knowledge_chunks`, finds rows
whose `embedding_model` differs from the currently-configured provider's
`model_id`, and re-embeds them in batches. Idempotent: re-running after a
clean pass finds zero mismatches.

Usage:
    cd services/orchestrator
    set -a && source ../../.env && set +a
    uv run python -m app.management.reembed                 # report only
    uv run python -m app.management.reembed --apply         # actually re-embed
    uv run python -m app.management.reembed --apply --batch-size 16
    uv run python -m app.management.reembed --tenant-slug acme --apply

Safety:
    - Default is REPORT mode. `--apply` is required to write anything.
    - Per-tenant scoping via `--tenant-slug` keeps the blast radius small;
      omit to re-embed across every tenant + shared corpora.
    - Each batch is its own transaction; partial failure leaves the DB
      in a consistent state (some chunks re-embedded, others not — the
      next run picks up where this one left off).
"""

import argparse
import asyncio
import json
import os
import sys

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import db_session
from app.knowledge.embeddings import make_embedding_provider
from app.knowledge.models import KnowledgeChunk
from app.tenancy.models import Tenant

logger = structlog.get_logger(__name__)


async def _fetch_mismatched(
    session: AsyncSession,
    active_model_id: str,
    tenant_id_filter: str | None,
    *,
    limit: int | None = None,
) -> list[KnowledgeChunk]:
    """Rows whose embedding_model != the active provider's model_id."""
    stmt = select(KnowledgeChunk).where(
        KnowledgeChunk.embedding_model != active_model_id
    )
    if tenant_id_filter == "__shared__":
        stmt = stmt.where(KnowledgeChunk.tenant_id.is_(None))
    elif tenant_id_filter is not None:
        stmt = stmt.where(KnowledgeChunk.tenant_id == tenant_id_filter)
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _reembed_batch(
    session: AsyncSession,
    rows: list[KnowledgeChunk],
    embedder,
) -> int:
    """Re-embed `rows` and write the new vectors back. Returns row count.

    Uses UPDATE ... WHERE id IN (...) so the chunk's content and metadata
    are untouched; only `embedding` + `embedding_model` change.
    """
    if not rows:
        return 0
    vectors = await embedder.embed([row.content for row in rows])
    for row, vector in zip(rows, vectors, strict=True):
        await session.execute(
            update(KnowledgeChunk)
            .where(KnowledgeChunk.id == row.id)
            .values(embedding=vector, embedding_model=embedder.model_id)
        )
    await session.commit()
    return len(rows)


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-embed knowledge chunks whose embedding_model is stale.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually re-embed. Default is REPORT-ONLY.",
    )
    parser.add_argument(
        "--tenant-slug",
        default=None,
        help=(
            "Restrict to one tenant's private chunks. Use '__shared__' for the "
            "shared corpora (tenant_id IS NULL). Omit to process every chunk."
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Chunks per embed call. Higher = faster but more memory peak.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Process at most N mismatched chunks (debug). Omit for unbounded."
        ),
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        sys.stderr.write(
            "ERROR: DATABASE_URL is not set. Source .env first:\n"
            "    set -a && source ../../.env && set +a\n"
        )
        return 2

    settings = get_settings()
    embedder = make_embedding_provider(settings)
    active_model_id = embedder.model_id

    tenant_filter: str | None = None
    if args.tenant_slug == "__shared__":
        tenant_filter = "__shared__"
    elif args.tenant_slug is not None:
        async with db_session() as session:
            t = (
                await session.execute(
                    select(Tenant).where(Tenant.slug == args.tenant_slug)
                )
            ).scalar_one_or_none()
            if t is None:
                sys.stderr.write(
                    f"ERROR: No tenant with slug={args.tenant_slug!r}\n"
                )
                return 3
            tenant_filter = str(t.id)

    if tenant_filter == "__shared__":
        scope_text = "__shared__ (tenant_id IS NULL)"
    elif tenant_filter:
        scope_text = tenant_filter
    else:
        scope_text = "all chunks (every tenant + shared)"
    mode_text = (
        "APPLY (will re-embed)" if args.apply
        else "REPORT-ONLY (use --apply to write)"
    )
    sys.stdout.write(f"Active embedder: {active_model_id}\n")
    sys.stdout.write(f"Scope: {scope_text}\n")
    sys.stdout.write(f"Mode:  {mode_text}\n\n")

    total_processed = 0
    while True:
        async with db_session() as session:
            batch = await _fetch_mismatched(
                session, active_model_id, tenant_filter,
                limit=args.batch_size,
            )
            if not batch:
                break

            if not args.apply:
                # Report-only: show what WOULD be re-embedded and stop.
                sys.stdout.write(
                    json.dumps(
                        {
                            "mismatched_in_first_batch": [
                                {
                                    "id": str(row.id),
                                    "current_model": row.embedding_model,
                                    "would_become": active_model_id,
                                    "source_type": row.source_type,
                                    "preview": row.content[:80],
                                }
                                for row in batch
                            ],
                            "total_in_first_batch_only": len(batch),
                            "next_step": "re-run with --apply to actually re-embed",
                        },
                        indent=2,
                    )
                    + "\n"
                )
                return 0

            count = await _reembed_batch(session, batch, embedder)
            total_processed += count
            sys.stdout.write(
                f"  re-embedded batch of {count} (total so far: {total_processed})\n"
            )
            if args.limit is not None and total_processed >= args.limit:
                sys.stdout.write(
                    f"  --limit {args.limit} reached; stopping\n"
                )
                break

    sys.stdout.write(f"\nDone. Total re-embedded: {total_processed}\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
