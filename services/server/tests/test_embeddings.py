"""OllamaEmbeddingAdapter + embedding factory knobs (qwen3-embedding wiring).

Hermetic — the HTTP boundary is stubbed with httpx.MockTransport (no live
Ollama). Covers the /api/embed endpoint contract probed live 2026-07-11:
batched `input`, optional MRL `dimensions` (server-side truncate+renormalize
for MRL-trained models like qwen3-embedding, native 4096 → 768), and the
instruction-aware query prefix (queries prefixed, passages raw).
"""

import json
from typing import Any

import httpx
import pytest
from wolf_server.config import Settings
from wolf_server.knowledge import embeddings
from wolf_server.knowledge.embeddings import OllamaEmbeddingAdapter
from wolf_server.knowledge.store import PgvectorKnowledgeStore

_DIM = 8  # small vectors keep the fixtures readable; the contract is the same


def _adapter_with_capture(
    captured: list[dict[str, Any]],
    *,
    response_dim: int = _DIM,
    **kwargs: Any,
) -> OllamaEmbeddingAdapter:
    """An adapter whose HTTP boundary records every request body and answers
    with one `response_dim`-wide vector per input."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append({"path": request.url.path, "body": body})
        vectors = [[float(i)] * response_dim for i, _ in enumerate(body["input"])]
        return httpx.Response(200, json={"embeddings": vectors})

    return OllamaEmbeddingAdapter(
        "http://ollama.test",
        model="qwen3-embedding:latest",
        dimension=_DIM,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_adapter_posts_one_batched_embed_request() -> None:
    captured: list[dict[str, Any]] = []
    adapter = _adapter_with_capture(captured)
    vectors = await adapter.embed(["alpha", "beta"])
    assert len(captured) == 1
    assert captured[0]["path"] == "/api/embed"  # modern batched endpoint
    assert captured[0]["body"]["input"] == ["alpha", "beta"]
    # dimensions is NOT sent unless configured — a non-MRL model must never
    # be silently truncated.
    assert "dimensions" not in captured[0]["body"]
    assert [len(v) for v in vectors] == [_DIM, _DIM]
    assert vectors[0] != vectors[1]  # order preserved


@pytest.mark.asyncio
async def test_adapter_sends_dimensions_when_mrl_truncation_configured() -> None:
    captured: list[dict[str, Any]] = []
    adapter = _adapter_with_capture(captured, request_dimensions=_DIM)
    await adapter.embed(["alpha"])
    assert captured[0]["body"]["dimensions"] == _DIM


@pytest.mark.asyncio
async def test_adapter_sub_batches_large_inputs() -> None:
    # 70 inputs → 32 + 32 + 6: one request can never outlive the HTTP
    # timeout just because a seed batch was large.
    captured: list[dict[str, Any]] = []
    adapter = _adapter_with_capture(captured)
    vectors = await adapter.embed([f"text-{i}" for i in range(70)])
    assert [len(c["body"]["input"]) for c in captured] == [32, 32, 6]
    assert len(vectors) == 70


@pytest.mark.asyncio
async def test_adapter_dim_mismatch_refusal_names_the_mrl_knob() -> None:
    # The live failure shape: qwen3-embedding without the knob returns its
    # native dimension. The guided error must name the fix.
    captured: list[dict[str, Any]] = []
    adapter = _adapter_with_capture(captured, response_dim=4096)
    with pytest.raises(ValueError, match="EMBEDDING_REQUEST_DIMENSIONS"):
        await adapter.embed(["alpha"])


@pytest.mark.asyncio
async def test_adapter_query_prefix_applies_to_queries_only() -> None:
    captured: list[dict[str, Any]] = []
    prefix = "Instruct: retrieve relevant passages\nQuery: "
    adapter = _adapter_with_capture(captured, query_prefix=prefix)
    await adapter.embed(["a passage"])  # passages embed RAW
    await adapter.embed_query("who restarted agent 002?")
    assert captured[0]["body"]["input"] == ["a passage"]
    assert captured[1]["body"]["input"] == [prefix + "who restarted agent 002?"]


def test_factory_passes_mrl_and_prefix_knobs_per_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Primary and aux each carry their OWN knobs — e.g. a native-768 nomic
    # primary next to an MRL qwen3-embedding aux.
    built: list[dict[str, Any]] = []

    class _Recorder:
        def __init__(self, base_url: str, *, model: str, **kwargs: Any) -> None:
            built.append({"model": model, **kwargs})

    monkeypatch.setattr(embeddings, "OllamaEmbeddingAdapter", _Recorder)
    settings = Settings(
        embedding_provider="ollama",
        embedding_model="nomic-embed-text",
        embedding_model_aux="qwen3-embedding:latest",
        embedding_request_dimensions=0,
        embedding_request_dimensions_aux=768,
        embedding_query_prefix="",
        embedding_query_prefix_aux="Instruct: x\nQuery: ",
    )
    embeddings.make_embedding_provider(settings)
    embeddings.make_embedding_provider_aux(settings)
    assert built[0]["model"] == "nomic-embed-text"
    assert built[0]["request_dimensions"] == 0
    assert built[0]["query_prefix"] == ""
    assert built[1]["model"] == "qwen3-embedding:latest"
    assert built[1]["request_dimensions"] == 768
    assert built[1]["query_prefix"] == "Instruct: x\nQuery: "


@pytest.mark.asyncio
async def test_store_prefers_embed_query_for_the_query_side() -> None:
    # Instruction-aware providers expose embed_query; the store must route
    # QUERIES through it (and passages through embed) so asymmetric models
    # actually get their prefix at search time.
    class _Asymmetric:
        def __init__(self) -> None:
            self.query_calls: list[str] = []

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * _DIM for _ in texts]

        async def embed_query(self, query: str) -> list[float]:
            self.query_calls.append(query)
            return [1.0] * _DIM

    class _BatchOnly:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[2.0] * _DIM for _ in texts]

    asymmetric = _Asymmetric()
    vector = await PgvectorKnowledgeStore._embed_query(asymmetric, "the query")
    assert asymmetric.query_calls == ["the query"]
    assert vector == [1.0] * _DIM
    # Fallback: an embedder without embed_query still works (older adapters,
    # stubs) via the batch method.
    fallback = await PgvectorKnowledgeStore._embed_query(_BatchOnly(), "q")
    assert fallback == [2.0] * _DIM


@pytest.mark.asyncio
async def test_adapter_document_prefix_applies_to_passages_only() -> None:
    # nomic-family task prefixes: documents get "search_document: ",
    # queries "search_query: " — never crossed, never doubled.
    captured: list[dict[str, Any]] = []
    adapter = _adapter_with_capture(
        captured,
        document_prefix="search_document: ",
        query_prefix="search_query: ",
    )
    await adapter.embed(["a passage"])
    await adapter.embed_query("a question")
    assert captured[0]["body"]["input"] == ["search_document: a passage"]
    assert captured[1]["body"]["input"] == ["search_query: a question"]


@pytest.mark.asyncio
async def test_adapter_num_ctx_forwarded_as_ollama_options() -> None:
    # The embedding model's context window is a per-request Ollama option;
    # without it Ollama truncates at the loaded default.
    captured: list[dict[str, Any]] = []
    adapter = _adapter_with_capture(captured, num_ctx=40960)
    await adapter.embed(["alpha"])
    assert captured[0]["body"]["options"] == {"num_ctx": 40960}
    # And absent when unset — the model's own default stays in charge.
    captured.clear()
    plain = _adapter_with_capture(captured)
    await plain.embed(["alpha"])
    assert "options" not in captured[0]["body"]


@pytest.mark.asyncio
async def test_adapter_char_limit_truncates_before_the_prefix() -> None:
    # The cap guards small-window models (v2-moe: 512 tokens). It must cut
    # the CONTENT, never the task prefix.
    captured: list[dict[str, Any]] = []
    adapter = _adapter_with_capture(
        captured, max_input_chars=5, document_prefix="search_document: "
    )
    await adapter.embed(["abcdefghij"])
    assert captured[0]["body"]["input"] == ["search_document: abcde"]


def test_factory_passes_dimension_and_new_knobs_per_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ADR 0033: EVERY knob is per-embedder — including the column width.
    # A 4096-dim qwen primary can sit next to a 768-dim v2-moe aux.
    built: list[dict[str, Any]] = []

    class _Recorder:
        def __init__(self, base_url: str, *, model: str, **kwargs: Any) -> None:
            built.append({"model": model, **kwargs})

    monkeypatch.setattr(embeddings, "OllamaEmbeddingAdapter", _Recorder)
    settings = Settings(
        embedding_provider="ollama",
        embedding_model="qwen3-embedding:latest",
        embedding_dimension=4096,
        embedding_num_ctx=40960,
        embedding_model_aux="nomic-embed-text-v2-moe",
        embedding_dimension_aux=768,
        embedding_document_prefix_aux="search_document: ",
        embedding_query_prefix_aux="search_query: ",
        embedding_char_limit_aux=1800,
        embedding_num_ctx_aux=512,
    )
    embeddings.make_embedding_provider(settings)
    embeddings.make_embedding_provider_aux(settings)
    assert built[0]["dimension"] == 4096
    assert built[0]["num_ctx"] == 40960
    assert built[0]["document_prefix"] == ""
    assert built[1]["dimension"] == 768
    assert built[1]["document_prefix"] == "search_document: "
    assert built[1]["query_prefix"] == "search_query: "
    assert built[1]["max_input_chars"] == 1800
    assert built[1]["num_ctx"] == 512


def test_aux_dimension_defaults_to_primary_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[dict[str, Any]] = []

    class _Recorder:
        def __init__(self, base_url: str, *, model: str, **kwargs: Any) -> None:
            built.append({"model": model, **kwargs})

    monkeypatch.setattr(embeddings, "OllamaEmbeddingAdapter", _Recorder)
    settings = Settings(
        embedding_provider="ollama",
        embedding_dimension=1024,
        embedding_model_aux="nomic-embed-text-v2-moe",
        embedding_dimension_aux=0,
    )
    embeddings.make_embedding_provider_aux(settings)
    assert built[0]["dimension"] == 1024


def test_knowledge_chunk_columns_follow_configured_dimensions() -> None:
    # The ORM declaration is settings-driven (ADR 0033): whatever the
    # process was configured with at import time IS the declared width.
    # Read via the NARROW EmbeddingDimensions loader — full Settings would
    # run the SECRET_KEY guard in secretless contexts (CI alembic-check).
    from wolf_server.config import get_embedding_dimensions
    from wolf_server.knowledge.models import KnowledgeChunk

    dims = get_embedding_dimensions()
    expected_aux = dims.embedding_dimension_aux or dims.embedding_dimension
    assert KnowledgeChunk.__table__.c.embedding.type.dim == dims.embedding_dimension
    assert KnowledgeChunk.__table__.c.embedding_v2.type.dim == expected_aux


def test_reembed_force_selects_every_row_via_keyset() -> None:
    # --force drops the model-stamp filter (prefix/MRL changes alter the
    # geometry without changing model_id) and paginates by id so rewritten
    # rows can't be picked up twice.
    import uuid as _uuid

    from wolf_server.management import reembed as reembed_module

    class _Result:
        def scalars(self) -> "_Result":
            return self

        def all(self) -> list[Any]:
            return []

    class _Session:
        def __init__(self) -> None:
            self.statements: list[Any] = []

        async def execute(self, stmt: Any) -> _Result:
            self.statements.append(stmt)
            return _Result()

    async def _run() -> tuple[str, str]:
        session = _Session()
        await reembed_module._fetch_mismatched(
            session,  # type: ignore[arg-type]
            "ollama:nomic-embed-text",
            None,
            is_aux=False,
            force=True,
            after_id=None,
        )
        first = str(session.statements[0])
        await reembed_module._fetch_mismatched(
            session,  # type: ignore[arg-type]
            "ollama:nomic-embed-text",
            None,
            is_aux=False,
            force=True,
            after_id=_uuid.uuid4(),
        )
        second = str(session.statements[1])
        return first, second

    import asyncio as _asyncio

    first, second = _asyncio.run(_run())
    assert "WHERE" not in first  # no stamp filter at all under force
    assert "ORDER BY knowledge_chunks.id" in first
    assert "knowledge_chunks.id >" in second  # keyset cursor advances


def _bq_store_and_session() -> "tuple[PgvectorKnowledgeStore, Any]":
    """A store whose primary embedder declares a 4096-dim column, with a
    session stub that records every statement and returns no rows."""

    class _Result:
        def all(self) -> list[Any]:
            return []

    class _Session:
        def __init__(self) -> None:
            self.statements: list[str] = []

        async def execute(self, stmt: Any) -> _Result:
            # Compile with the REAL postgresql dialect — BIT(n) is a
            # postgres-specific type the generic compiler renders bare.
            from sqlalchemy.dialects import postgresql

            self.statements.append(str(stmt.compile(dialect=postgresql.dialect())))
            return _Result()

    class _WideEmbedder:
        dimension = 4096

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 4096 for _ in texts]

    session = _Session()
    store = PgvectorKnowledgeStore(
        session,  # type: ignore[arg-type]
        _WideEmbedder(),
        embedder_aux=_WideEmbedder(),
        bq_oversample=4,
    )
    return store, session


@pytest.mark.asyncio
async def test_wide_primary_leg_uses_binary_quantized_two_stage() -> None:
    # Columns wider than the 2000-dim HNSW cap must query via the SAME
    # binary_quantize(...)::bit(N) expression the schema tool indexes
    # (Hamming stage), oversampled, then rerank by exact cosine.
    import uuid as _uuid

    store, session = _bq_store_and_session()
    ranks = await store._vector_candidates(_uuid.uuid4(), [0.0] * 4096, None, None)
    assert ranks == {}  # stage 1 returned nothing -> empty leg, no stage 2
    assert len(session.statements) == 1
    stage1 = session.statements[0]
    assert "binary_quantize" in stage1
    assert "BIT(4096)" in stage1
    assert "<~>" in stage1  # Hamming distance drives the indexed stage


@pytest.mark.asyncio
async def test_wide_aux_leg_uses_bq_and_keeps_the_not_null_guard() -> None:
    import uuid as _uuid

    store, session = _bq_store_and_session()
    ranks = await store._vector_aux_candidates(_uuid.uuid4(), [0.0] * 4096, None, None)
    assert ranks == {}
    stage1 = session.statements[0]
    assert "binary_quantize" in stage1
    assert "embedding_v2 IS NOT NULL" in stage1  # NULL aux rows never rank


@pytest.mark.asyncio
async def test_narrow_embedder_keeps_the_plain_cosine_leg() -> None:
    # 768-dim (and dimension-less test stubs) stay on the single-stage
    # cosine query — BQ is strictly for widths above the HNSW cap.
    import uuid as _uuid

    class _Result:
        def all(self) -> list[Any]:
            return []

    class _Session:
        def __init__(self) -> None:
            self.statements: list[str] = []

        async def execute(self, stmt: Any) -> _Result:
            self.statements.append(str(stmt))
            return _Result()

    class _NarrowEmbedder:
        dimension = 768

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 768 for _ in texts]

    session = _Session()
    store = PgvectorKnowledgeStore(session, _NarrowEmbedder())  # type: ignore[arg-type]
    await store._vector_candidates(_uuid.uuid4(), [0.0] * 768, None, None)
    assert len(session.statements) == 1
    assert "binary_quantize" not in session.statements[0]
