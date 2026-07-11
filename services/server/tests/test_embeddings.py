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
