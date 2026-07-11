"""Reconcile the pgvector column widths with the configured embedding stack.

ADR 0033: the embedding dimensions are fully configurable —
`EMBEDDING_DIMENSION` (primary `knowledge_chunks.embedding`) and
`EMBEDDING_DIMENSION_AUX` (secondary `embedding_v2`) drive both the ORM
declaration and, through THIS tool, the live database schema. Switching
embedding models therefore switches *everything*: column widths, HNSW
indexes, and the stored vectors themselves.

What an apply does, per out-of-sync column:
  1. Drops the column's HNSW index.
  2. Drops NOT NULL (primary only), re-types the column to
     `vector(<configured>)` with `USING NULL` — a width change invalidates
     every stored vector, so they are cleared, and the model stamps are
     reset so `reembed` sees the rows as stale.
  3. Re-embeds every cleared vector with the active provider(s) in
     batched, per-batch-committed passes (resumable: re-running continues
     where a crash stopped).
  4. Restores NOT NULL (primary; only when every row re-embedded cleanly)
     and rebuilds the HNSW index — unless the new width exceeds pgvector's
     HNSW cap (2000 dims), in which case the index is skipped and search
     runs exact (perfect recall, slower on very large corpora; stated
     honestly in the output).

Resumable by design: the plan is computed from the LIVE schema state
(width, NULL vectors, NOT NULL constraint, index presence), so a re-run
after a partial failure picks up the remaining steps instead of redoing
finished ones.

Usage:
    cd services/server
    set -a && source ../../.env && set +a
    uv run python -m wolf_server.management.embedding_schema           # report
    uv run python -m wolf_server.management.embedding_schema --apply   # do it

Safety:
    - Default is REPORT mode; `--apply` is required to touch anything.
    - Retrieval is degraded while vectors are cleared (BM25 keeps
      working; vector legs return nothing until re-embedding finishes).
      Run it in a maintenance window.
    - The aux column is only re-embedded when EMBEDDING_MODEL_AUX is
      configured; otherwise it is re-typed and left NULL.
"""

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uuid

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from wolf_server.config import get_settings
from wolf_server.database import db_session
from wolf_server.knowledge.embeddings import (
    EmbeddingProvider,
    make_embedding_provider,
    make_embedding_provider_aux,
)
from wolf_server.knowledge.models import KnowledgeChunk

_TABLE = "knowledge_chunks"

# pgvector's HNSW (and IVFFlat) indexes support at most 2000 dimensions on
# the `vector` type. Beyond that, queries run as exact scans — correct
# results, linear cost. (halfvec raises the cap to 4000 but changes the
# stored type; deliberately out of scope until a corpus actually needs it.)
HNSW_MAX_DIMENSION = 2000

_INDEX_NAMES = {
    "embedding": "ix_knowledge_chunks_embedding_hnsw",
    "embedding_v2": "ix_knowledge_chunks_embedding_v2_hnsw",
}
_MODEL_STAMP_COLUMNS = {
    "embedding": "embedding_model",
    "embedding_v2": "embedding_v2_model",
}

_VECTOR_TYPE_RE = re.compile(r"^vector\((\d+)\)$")


@dataclass(frozen=True)
class ColumnState:
    """Live schema facts for one vector column, read from pg_catalog."""

    name: str
    live_dimension: int
    not_null: bool
    index_present: bool
    null_vector_count: int


@dataclass(frozen=True)
class ColumnPlan:
    """The reconciliation steps one column needs (empty = in sync)."""

    name: str
    target_dimension: int
    retype: bool
    reembed_nulls: bool
    restore_not_null: bool
    build_index: bool
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def in_sync(self) -> bool:
        return not (self.retype or self.reembed_nulls or self.restore_not_null or self.build_index)


async def read_column_states(session: AsyncSession) -> dict[str, ColumnState]:
    """Read width / nullability / index / NULL-count for both vector columns."""
    rows = (
        await session.execute(
            text(
                "SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull "
                "FROM pg_attribute a JOIN pg_class c ON a.attrelid = c.oid "
                "WHERE c.relname = :table AND a.attname IN ('embedding', 'embedding_v2') "
                "AND NOT a.attisdropped"
            ),
            {"table": _TABLE},
        )
    ).all()
    indexes = {
        row[0]
        for row in (
            await session.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = :table"),
                {"table": _TABLE},
            )
        ).all()
    }
    states: dict[str, ColumnState] = {}
    for name, type_text, not_null in rows:
        match = _VECTOR_TYPE_RE.match(type_text)
        if match is None:
            raise RuntimeError(
                f"{_TABLE}.{name} has type {type_text!r}, expected vector(N). "
                "The schema tool only manages pgvector columns — inspect the "
                "database by hand."
            )
        null_count_row = await session.execute(
            text(f"SELECT count(*) FROM {_TABLE} WHERE {name} IS NULL")  # noqa: S608
        )
        states[name] = ColumnState(
            name=name,
            live_dimension=int(match.group(1)),
            not_null=bool(not_null),
            index_present=_INDEX_NAMES[name] in indexes,
            null_vector_count=int(null_count_row.scalar_one()),
        )
    missing = {"embedding", "embedding_v2"} - states.keys()
    if missing:
        raise RuntimeError(
            f"{_TABLE} is missing column(s) {sorted(missing)} — run `alembic upgrade head` first."
        )
    return states


def build_column_plan(
    state: ColumnState,
    target_dimension: int,
    *,
    wants_not_null: bool,
    has_embedder: bool,
) -> ColumnPlan:
    """Derive the steps that bring one column in line with settings.

    Computed from LIVE facts so a re-run after a partial failure resumes:
    a column already at the target width but holding NULL vectors (or
    missing its constraint/index) gets exactly the remaining steps.
    """
    retype = state.live_dimension != target_dimension
    nulls_after = state.null_vector_count > 0 or retype
    reembed_nulls = nulls_after and has_embedder
    notes: list[str] = []
    if nulls_after and not has_embedder:
        notes.append(
            f"{state.name}: vectors are/will be NULL but no embedder is "
            "configured for this column — leaving NULL (search skips the leg)."
        )
    # NOT NULL is restored whenever the constraint is (or will be) absent —
    # the apply step itself refuses to SET NOT NULL unless the re-embed
    # finished clean, so a resume run lands here with zero NULLs and just
    # restores the constraint.
    restore_not_null = wants_not_null and (retype or not state.not_null)
    index_possible = target_dimension <= HNSW_MAX_DIMENSION
    build_index = index_possible and (retype or not state.index_present)
    if not index_possible:
        notes.append(
            f"{state.name}: {target_dimension} dims exceeds pgvector's HNSW cap "
            f"({HNSW_MAX_DIMENSION}) — no ANN index; searches run exact "
            "(perfect recall, linear cost)."
        )
    return ColumnPlan(
        name=state.name,
        target_dimension=target_dimension,
        retype=retype,
        reembed_nulls=reembed_nulls,
        restore_not_null=restore_not_null,
        build_index=build_index,
        notes=tuple(notes),
    )


async def _retype_column(session: AsyncSession, plan: ColumnPlan) -> None:
    """Drop index, clear + re-type the column, reset the model stamps."""
    await session.execute(text(f"DROP INDEX IF EXISTS {_INDEX_NAMES[plan.name]}"))
    if plan.name == "embedding":
        await session.execute(text(f"ALTER TABLE {_TABLE} ALTER COLUMN embedding DROP NOT NULL"))
    stamp = _MODEL_STAMP_COLUMNS[plan.name]
    await session.execute(
        text(
            f"ALTER TABLE {_TABLE} ALTER COLUMN {plan.name} "  # noqa: S608
            f"TYPE vector({plan.target_dimension}) USING NULL"
        )
    )
    # Clearing the stamp keeps `reembed`'s model-mismatch detection honest:
    # a NULL vector must never look "already embedded by the active model".
    await session.execute(
        text(f"UPDATE {_TABLE} SET {stamp} = NULL WHERE {plan.name} IS NULL")  # noqa: S608
    )
    await session.commit()


async def _reembed_null_vectors(
    plan: ColumnPlan,
    embedder: EmbeddingProvider,
    batch_size: int,
) -> tuple[int, int]:
    """Fill every NULL vector in `plan.name`; returns (succeeded, failed).

    Keyset-paginated by id so a chunk the embedder rejects doesn't loop
    forever — it stays NULL, is counted as failed, and the caller decides
    whether NOT NULL can be restored.
    """
    column = getattr(KnowledgeChunk, plan.name)
    succeeded = 0
    failed = 0
    last_id: uuid.UUID | None = None
    while True:
        async with db_session() as session:
            stmt = (
                select(KnowledgeChunk)
                .where(column.is_(None))
                .order_by(KnowledgeChunk.id)
                .limit(batch_size)
            )
            if last_id is not None:
                stmt = stmt.where(KnowledgeChunk.id > last_id)
            rows = list((await session.execute(stmt)).scalars().all())
            if not rows:
                break
            last_id = rows[-1].id
            for row in rows:
                try:
                    vector = (await embedder.embed([row.content]))[0]
                except Exception as exc:
                    failed += 1
                    sys.stderr.write(
                        f"  [warn] embed failed for chunk {row.id} "
                        f"({type(exc).__name__}); leaving NULL\n"
                    )
                    continue
                values: dict[str, object] = {
                    plan.name: vector,
                    _MODEL_STAMP_COLUMNS[plan.name]: embedder.model_id,
                }
                await session.execute(
                    update(KnowledgeChunk).where(KnowledgeChunk.id == row.id).values(**values)
                )
                succeeded += 1
            await session.commit()
            sys.stdout.write(f"  {plan.name}: re-embedded {succeeded} (failed {failed}) so far\n")
    return succeeded, failed


async def _finalize_column(session: AsyncSession, plan: ColumnPlan, *, clean: bool) -> None:
    """Restore NOT NULL (primary, only when clean) + rebuild the HNSW index."""
    if plan.restore_not_null:
        if clean:
            await session.execute(text(f"ALTER TABLE {_TABLE} ALTER COLUMN embedding SET NOT NULL"))
        else:
            sys.stderr.write(
                "  [warn] some chunks failed to re-embed — NOT NULL stays off "
                "for `embedding`; fix the embedder and re-run this tool.\n"
            )
    if plan.build_index:
        await session.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {_INDEX_NAMES[plan.name]} "
                f"ON {_TABLE} USING hnsw ({plan.name} vector_cosine_ops)"
            )
        )
    await session.commit()


def _describe(plan: ColumnPlan, state: ColumnState) -> str:
    if plan.in_sync:
        return f"{plan.name}: vector({state.live_dimension}) — in sync"
    steps: list[str] = []
    if plan.retype:
        steps.append(
            f"re-type vector({state.live_dimension}) -> vector({plan.target_dimension}) "
            "(clears stored vectors)"
        )
    if plan.reembed_nulls:
        steps.append("re-embed cleared/NULL vectors")
    if plan.restore_not_null:
        steps.append("restore NOT NULL")
    if plan.build_index:
        steps.append("(re)build HNSW index")
    return f"{plan.name}: " + "; ".join(steps)


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile knowledge_chunks vector columns with EMBEDDING_DIMENSION(_AUX).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually alter the schema + re-embed. Default is REPORT-ONLY.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Chunks per re-embed batch (each batch is its own commit).",
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        sys.stderr.write(
            "ERROR: DATABASE_URL is not set. Source .env first:\n"
            "    set -a && source ../../.env && set +a\n"
        )
        return 2

    settings = get_settings()
    primary_dim = settings.embedding_dimension
    aux_dim = settings.embedding_dimension_aux or primary_dim
    embedder = make_embedding_provider(settings)
    embedder_aux = make_embedding_provider_aux(settings)

    async with db_session() as session:
        states = await read_column_states(session)

    plans = {
        "embedding": build_column_plan(
            states["embedding"], primary_dim, wants_not_null=True, has_embedder=True
        ),
        "embedding_v2": build_column_plan(
            states["embedding_v2"],
            aux_dim,
            wants_not_null=False,
            has_embedder=embedder_aux is not None,
        ),
    }

    sys.stdout.write(f"Configured: embedding=vector({primary_dim}), ")
    sys.stdout.write(f"embedding_v2=vector({aux_dim})\n")
    for name, plan in plans.items():
        sys.stdout.write(f"  {_describe(plan, states[name])}\n")
        for note in plan.notes:
            sys.stdout.write(f"    note: {note}\n")

    if all(plan.in_sync for plan in plans.values()):
        sys.stdout.write("\nSchema matches settings — nothing to do.\n")
        return 0
    if not args.apply:
        sys.stdout.write(
            "\nREPORT-ONLY. Re-run with --apply to execute (retrieval is "
            "degraded while vectors are cleared — use a maintenance window).\n"
        )
        return 0

    overall_failed = 0
    for name, plan in plans.items():
        if plan.in_sync:
            continue
        sys.stdout.write(f"\n=== {name} ===\n")
        if plan.retype:
            async with db_session() as session:
                await _retype_column(session, plan)
            sys.stdout.write(f"  re-typed to vector({plan.target_dimension})\n")
        failed = 0
        if plan.reembed_nulls:
            column_embedder = embedder if name == "embedding" else embedder_aux
            assert column_embedder is not None  # guarded by has_embedder  # noqa: S101
            _, failed = await _reembed_null_vectors(plan, column_embedder, args.batch_size)
            overall_failed += failed
        async with db_session() as session:
            await _finalize_column(session, plan, clean=failed == 0)

    async with db_session() as session:
        after = await read_column_states(session)
    sys.stdout.write("\nFinal state:\n")
    for name, state in after.items():
        sys.stdout.write(
            f"  {name}: vector({state.live_dimension}), "
            f"not_null={state.not_null}, index={state.index_present}, "
            f"null_vectors={state.null_vector_count}\n"
        )
    if overall_failed:
        sys.stderr.write(
            f"\n{overall_failed} chunk(s) failed to re-embed — re-run this tool "
            "after fixing the embedder; it resumes from the live state.\n"
        )
        return 1
    sys.stdout.write("\nDone — schema and vectors match the configured embedding stack.\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
