"""embedding_schema tool — plan derivation + live-state introspection (ADR 0033).

Hermetic: sessions are stubbed at the execute() boundary. The plan logic is
pure (ColumnState in, ColumnPlan out), so the interesting matrix — retype,
resume-after-crash, the HNSW 2000-dim cap, aux-without-embedder — is covered
without a database.
"""

from typing import Any

import pytest
from wolf_server.management.embedding_schema import (
    HNSW_MAX_DIMENSION,
    ColumnState,
    build_column_plan,
    read_column_states,
)


def _state(
    name: str = "embedding",
    *,
    dim: int = 768,
    not_null: bool = True,
    index: bool = True,
    nulls: int = 0,
) -> ColumnState:
    return ColumnState(
        name=name,
        live_dimension=dim,
        not_null=not_null,
        index_present=index,
        null_vector_count=nulls,
    )


def test_in_sync_column_plans_nothing() -> None:
    plan = build_column_plan(_state(), 768, wants_not_null=True, has_embedder=True)
    assert plan.in_sync


def test_dimension_change_plans_the_full_sequence() -> None:
    plan = build_column_plan(_state(dim=768), 1024, wants_not_null=True, has_embedder=True)
    assert plan.retype
    assert plan.reembed_nulls
    assert plan.restore_not_null
    assert plan.build_index  # 1024 <= 2000: HNSW comes back


def test_above_hnsw_cap_skips_the_index_and_says_so() -> None:
    # qwen3-embedding native width: storable, searchable (exact scan), but
    # not ANN-indexable on the vector type.
    plan = build_column_plan(_state(dim=768), 4096, wants_not_null=True, has_embedder=True)
    assert plan.retype
    assert not plan.build_index
    assert any(str(HNSW_MAX_DIMENSION) in note for note in plan.notes)


def test_resume_after_crash_only_plans_whats_missing() -> None:
    # Crash after re-type + partial re-embed: width already correct, some
    # NULLs remain, constraint + index still absent. The plan must resume,
    # not redo the re-type.
    state = _state(dim=1024, not_null=False, index=False, nulls=42)
    plan = build_column_plan(state, 1024, wants_not_null=True, has_embedder=True)
    assert not plan.retype
    assert plan.reembed_nulls
    assert plan.restore_not_null
    assert plan.build_index


def test_resume_with_clean_vectors_just_restores_constraint_and_index() -> None:
    state = _state(dim=1024, not_null=False, index=False, nulls=0)
    plan = build_column_plan(state, 1024, wants_not_null=True, has_embedder=True)
    assert not plan.retype
    assert not plan.reembed_nulls
    assert plan.restore_not_null
    assert plan.build_index
    assert not plan.in_sync


def test_aux_without_embedder_leaves_nulls_and_notes_it() -> None:
    # Re-typing embedding_v2 while EMBEDDING_MODEL_AUX is unset: the column
    # switches width but stays NULL — search just skips the third leg.
    state = _state("embedding_v2", dim=768, not_null=False, index=True)
    plan = build_column_plan(state, 1024, wants_not_null=False, has_embedder=False)
    assert plan.retype
    assert not plan.reembed_nulls
    assert not plan.restore_not_null
    assert any("no embedder" in note for note in plan.notes)


@pytest.mark.asyncio
async def test_read_column_states_parses_live_catalog_shapes() -> None:
    # The pg_catalog answer shapes verified live 2026-07-11:
    # format_type() -> 'vector(768)', attnotnull boolean, pg_indexes names.
    class _Rows:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def all(self) -> list[Any]:
            return self._rows

        def scalar_one(self) -> int:
            return self._rows[0][0]

    class _Session:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, stmt: Any, params: Any = None) -> _Rows:
            sql = str(stmt)
            if "pg_attribute" in sql:
                return _Rows(
                    [
                        ("embedding", "vector(768)", True),
                        ("embedding_v2", "vector(1024)", False),
                    ]
                )
            if "pg_indexes" in sql:
                return _Rows([("ix_knowledge_chunks_embedding_hnsw",)])
            # NULL counts — one call per column.
            self.calls += 1
            return _Rows([(0 if self.calls == 1 else 7,)])

    states = await read_column_states(_Session())  # type: ignore[arg-type]
    assert states["embedding"].live_dimension == 768
    assert states["embedding"].not_null
    assert states["embedding"].index_present
    assert states["embedding"].null_vector_count == 0
    assert states["embedding_v2"].live_dimension == 1024
    assert not states["embedding_v2"].index_present
    assert states["embedding_v2"].null_vector_count == 7


@pytest.mark.asyncio
async def test_read_column_states_refuses_non_vector_columns() -> None:
    class _Rows:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def all(self) -> list[Any]:
            return self._rows

    class _Session:
        async def execute(self, stmt: Any, params: Any = None) -> _Rows:
            sql = str(stmt)
            if "pg_attribute" in sql:
                return _Rows([("embedding", "text", True)])
            return _Rows([])

    with pytest.raises(RuntimeError, match="expected vector"):
        await read_column_states(_Session())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_aux_null_count_excludes_unembeddable_sentinel() -> None:
    # v2-moe legitimately rejects some chunks (sentinel-stamped NULLs).
    # Those are steady state, not pending work — the plan must stay
    # idempotent on a corpus whose only aux NULLs are sentinels.
    captured: list[str] = []

    class _Rows:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def all(self) -> list[Any]:
            return self._rows

        def scalar_one(self) -> int:
            return 0

    class _Session:
        async def execute(self, stmt: Any, params: Any = None) -> _Rows:
            sql = str(stmt)
            if "pg_attribute" in sql:
                return _Rows(
                    [
                        ("embedding", "vector(768)", True),
                        ("embedding_v2", "vector(768)", False),
                    ]
                )
            if "pg_indexes" in sql:
                return _Rows(
                    [
                        ("ix_knowledge_chunks_embedding_hnsw",),
                        ("ix_knowledge_chunks_embedding_v2_hnsw",),
                    ]
                )
            captured.append(sql)
            return _Rows([(0,)])

    states = await read_column_states(_Session())  # type: ignore[arg-type]
    primary_sql = next(sql for sql in captured if "embedding_v2" not in sql)
    aux_sql = next(sql for sql in captured if "embedding_v2 IS NULL" in sql)
    assert "IS DISTINCT FROM" not in primary_sql  # primary NULLs always count
    assert "embedding_v2_model IS DISTINCT FROM :sentinel" in aux_sql
    assert states["embedding_v2"].null_vector_count == 0
