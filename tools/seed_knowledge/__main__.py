"""Driver CLI for the real-corpus seed ingest.

Usage:
    cd services/server
    set -a && source ../../.env && set +a
    uv run python -m tools.seed_knowledge --source attack
    uv run python -m tools.seed_knowledge --source wazuh_rules
    uv run python -m tools.seed_knowledge --source all
    uv run python -m tools.seed_knowledge --source all --replace-shared

Idempotency: by default the CLI keeps existing chunks and skips any whose
SHA-256(content) is already in the DB. `--replace-shared` first deletes
every shared-corpus chunk (tenant_id IS NULL) before re-ingesting — the
right choice when the source has materially changed (a new ATT&CK
version, a new Wazuh release). Tenant-private chunks are never touched.
"""

# ruff: noqa: T201

import argparse
import asyncio
import hashlib
import os
import sys
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import delete, select
from wolf_server.config import get_settings
from wolf_server.database import db_session
from wolf_server.knowledge.embeddings import make_embedding_provider
from wolf_server.knowledge.models import KnowledgeChunk
from wolf_server.knowledge.store import ChunkInput, PgvectorKnowledgeStore

from tools.seed_knowledge.attack import ingest_attack
from tools.seed_knowledge.wazuh_rules import ingest_wazuh_rules


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def _existing_shared_hashes() -> set[str]:
    """All SHA-256(content) of currently-indexed SHARED chunks.

    Idempotency leans on this: if a hash is already in the table, skip
    the new chunk. Tenant-private chunks are excluded from the scan
    because they're owned by the tenant lifecycle, not this CLI.
    """
    async with db_session() as session:
        stmt = select(KnowledgeChunk.content).where(KnowledgeChunk.tenant_id.is_(None))
        rows = await session.execute(stmt)
        return {_content_hash(content) for (content,) in rows.all()}


async def _replace_shared() -> int:
    """Delete every shared (tenant_id IS NULL) chunk. Returns row count."""
    async with db_session() as session:
        result = await session.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.tenant_id.is_(None))
        )
        await session.commit()
        return result.rowcount or 0


async def _ingest(chunks: Iterable[ChunkInput], *, batch_size: int = 32) -> int:
    """Embed + insert chunks in batches. Returns inserted count.

    Batching is per-embed-call, not per-DB-insert — each batch's
    embeddings are produced in one provider.embed(...) round, then
    flushed individually so a single bad chunk doesn't roll back the
    whole batch.
    """
    settings = get_settings()
    embedder = make_embedding_provider(settings)

    existing = await _existing_shared_hashes()
    inserted = 0
    skipped = 0
    pending: list[ChunkInput] = []
    for chunk in chunks:
        h = _content_hash(chunk.content)
        if h in existing:
            skipped += 1
            continue
        existing.add(h)
        pending.append(chunk)
        if len(pending) >= batch_size:
            inserted += await _flush_batch(pending, embedder)
            pending = []
    if pending:
        inserted += await _flush_batch(pending, embedder)
    print(f"  inserted={inserted}  skipped(already_present)={skipped}")
    return inserted


async def _flush_batch(batch: list[ChunkInput], embedder) -> int:
    async with db_session() as session:
        store = PgvectorKnowledgeStore(session, embedder)
        ids = await store.upsert(batch)
        return len(ids)


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest real corpus into knowledge_chunks.",
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=["attack", "wazuh_rules", "all"],
        help="Which corpus to ingest.",
    )
    parser.add_argument(
        "--replace-shared",
        action="store_true",
        help=(
            "Delete every shared (tenant_id IS NULL) chunk first. Required "
            "after a corpus version bump; otherwise re-running just appends "
            "new content via the content-hash idempotency check."
        ),
    )
    parser.add_argument(
        "--cache-dir",
        default=".local/seed_knowledge_cache",
        help="Where to cache downloaded source files (gitignored).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Max chunks per source (debug). E.g. --limit 50 to dry-run on a "
            "manageable subset before a full ingest."
        ),
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        sys.stderr.write(
            "ERROR: DATABASE_URL is not set. Source .env first:\n"
            "    set -a && source ../../.env && set +a\n"
        )
        return 2

    cache_dir = Path(args.cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    if args.replace_shared:
        n = await _replace_shared()
        print(f"Deleted {n} existing shared chunks (replace-shared mode).")

    total = 0
    if args.source in ("attack", "all"):
        print("=== Ingesting MITRE ATT&CK ===")
        chunks = ingest_attack(cache_dir=cache_dir, limit=args.limit)
        total += await _ingest(chunks)

    if args.source in ("wazuh_rules", "all"):
        print("=== Ingesting Wazuh ruleset ===")
        chunks = ingest_wazuh_rules(cache_dir=cache_dir, limit=args.limit)
        total += await _ingest(chunks)

    print(f"\nDone. Total chunks newly inserted across all sources: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
