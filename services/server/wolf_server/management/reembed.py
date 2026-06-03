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

from wolf_server.config import get_settings
from wolf_server.database import db_session
from wolf_server.knowledge.embeddings import (
    EmbeddingProvider,
    make_embedding_provider,
    make_embedding_provider_aux,
)
from wolf_server.knowledge.models import KnowledgeChunk
from wolf_server.tenancy.models import Tenant

logger = structlog.get_logger(__name__)


async def _fetch_mismatched(
    session: AsyncSession,
    active_model_id: str,
    tenant_id_filter: str | None,
    *,
    is_aux: bool,
    limit: int | None = None,
) -> list[KnowledgeChunk]:
    """Rows whose embedding model on the targeted column doesn't match.

    Primary mode (`is_aux=False`): selects rows where `embedding_model`
    differs from the active provider's `model_id`. Same as the
    pre-ADR-0014 behaviour.

    Aux mode (`is_aux=True`): selects rows where `embedding_v2_model`
    differs OR is NULL — i.e. rows that have never been embedded with
    the aux model OR were embedded with a different aux model. This
    is the path for "populate v2 vectors for the existing corpus."
    """
    if is_aux:
        # NULL or != active aux model. SQLAlchemy: IS DISTINCT FROM
        # treats NULL as a value, so it's the right operator here.
        stmt = select(KnowledgeChunk).where(
            KnowledgeChunk.embedding_v2_model.is_distinct_from(active_model_id)
        )
    else:
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


# Sentinel written to embedding_v2_model when the aux embedder rejected
# a chunk (e.g. v2-moe's 512-token limit). Letting the CLI keep retrying
# would be an infinite loop — instead we mark "tried and failed" so the
# WHERE-clause filter in _fetch_mismatched stops picking the row up.
# search() treats embedding_v2 IS NULL as "skip this leg," so the chunk
# is still retrievable via the primary vector + FTS legs (the whole
# point of the multi-embedding chained design).
AUX_UNEMBEDDABLE_SENTINEL = "__unembeddable__"

# Conservative truncation cap for aux embedders with smaller context.
# v2-moe's nominal limit is 512 tokens ≈ 2048 chars BPE; 1800 gives
# safety margin without losing too much detail.
AUX_CHAR_LIMIT = 1800


async def _reembed_batch(
    session: AsyncSession,
    rows: list[KnowledgeChunk],
    embedder: EmbeddingProvider,
    *,
    is_aux: bool,
) -> int:
    """Re-embed `rows` and write the new vectors back. Returns row count.

    Primary mode: updates `embedding` + `embedding_model`. No truncation
    (primary model is expected to handle every chunk's content).

    Aux mode: updates `embedding_v2` + `embedding_v2_model`. Truncates
    inputs at AUX_CHAR_LIMIT to avoid the 'unexpected EOF' Ollama
    returns for inputs that exceed v2-moe's 512-token window. If the
    aux model still rejects a chunk after truncation, that chunk is
    stamped with AUX_UNEMBEDDABLE_SENTINEL so future runs skip it.

    Either way, the chunk's content + metadata + primary vector are
    untouched. Each batch is its own commit so partial failure leaves
    a consistent state.
    """
    if not rows:
        return 0
    succeeded = 0
    for row in rows:
        embed_input = row.content
        if is_aux and len(embed_input) > AUX_CHAR_LIMIT:
            embed_input = embed_input[:AUX_CHAR_LIMIT]
        try:
            vector = (await embedder.embed([embed_input]))[0]
        except Exception as exc:
            sys.stderr.write(
                f"  [warn] {'aux' if is_aux else 'primary'} embed failed for "
                f"chunk {row.id} ({type(exc).__name__}); "
                f"{'marking unembeddable' if is_aux else 'skipping'}\n"
            )
            if is_aux:
                # Sentinel ensures future runs DON'T retry this forever.
                await session.execute(
                    update(KnowledgeChunk)
                    .where(KnowledgeChunk.id == row.id)
                    .values(
                        embedding_v2=None,
                        embedding_v2_model=AUX_UNEMBEDDABLE_SENTINEL,
                    )
                )
            continue
        if is_aux:
            await session.execute(
                update(KnowledgeChunk)
                .where(KnowledgeChunk.id == row.id)
                .values(embedding_v2=vector, embedding_v2_model=embedder.model_id)
            )
        else:
            await session.execute(
                update(KnowledgeChunk)
                .where(KnowledgeChunk.id == row.id)
                .values(embedding=vector, embedding_model=embedder.model_id)
            )
        succeeded += 1
    await session.commit()
    return succeeded


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
        "--aux",
        action="store_true",
        help=(
            "Operate on the SECONDARY (embedding_v2) column instead of "
            "the primary. Used to populate v2 vectors for the corpus "
            "after enabling ADR 0014's multi-embedding retrieval — "
            "the operator first sets EMBEDDING_MODEL_AUX in .env, then "
            "runs `reembed --aux --apply` to fill the new column."
        ),
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
    if args.aux:
        embedder = make_embedding_provider_aux(settings)
        if embedder is None:
            sys.stderr.write(
                "ERROR: --aux requested but EMBEDDING_MODEL_AUX is not set. "
                "Add it to .env (e.g. EMBEDDING_MODEL_AUX=nomic-embed-text-v2-moe) "
                "and try again.\n"
            )
            return 4
    else:
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

    column_label = "embedding_v2" if args.aux else "embedding"
    sys.stdout.write(f"Column: {column_label}\n\n")

    total_processed = 0
    while True:
        async with db_session() as session:
            batch = await _fetch_mismatched(
                session, active_model_id, tenant_filter,
                is_aux=args.aux,
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
                                    "current_model": (
                                        row.embedding_v2_model if args.aux
                                        else row.embedding_model
                                    ),
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

            count = await _reembed_batch(session, batch, embedder, is_aux=args.aux)
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
