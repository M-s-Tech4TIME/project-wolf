"""Embedding provider abstraction + Ollama-hosted + in-process implementations.

The protocol hides the runtime (Ollama HTTP, sentence-transformers in-process,
fastembed/ONNX tomorrow). The contract is: embed a batch of texts, return a
list of fixed-dimension vectors in the same order. Dimension is reported via
`dimension` and must equal the embedder's own pgvector column width
(settings-driven per ADR 0033: `EMBEDDING_DIMENSION` for the primary,
`EMBEDDING_DIMENSION_AUX` for the aux column).

Per doc 06, "tie chunk records to embedding-model identity, so changing the
embedding model triggers a planned re-embedding rather than silent
inconsistency." `model_id` is returned alongside vectors and stamped on
every KnowledgeChunk row.

Provider selection is env-driven via `EMBEDDING_PROVIDER` (see config.py).
The sentence-transformers path requires the optional `embeddings-local`
extra (`uv sync --extra embeddings-local`); the import is lazy so the
default install does NOT require torch.
"""

import asyncio
from typing import TYPE_CHECKING, Any, Protocol

import httpx

if TYPE_CHECKING:
    # Type-checking-only — real import is lazy inside the adapter.
    from wolf_server.config import Settings


class EmbeddingProvider(Protocol):
    """Returns fixed-dimension vectors for a batch of input texts."""

    @property
    def model_id(self) -> str:
        """Stable identifier stamped on every chunk this provider embeds."""

    @property
    def dimension(self) -> int:
        """Vector dimension; must match the DB column width."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of PASSAGES (the document prefix, when configured,
        applies). Output order matches input."""

    async def embed_query(self, query: str) -> list[float]:
        """Embed a QUERY — instruction-aware providers apply their query
        prefix here (asymmetric retrieval); symmetric providers just embed."""


# One /api/embed request carries at most this many inputs — bounds a single
# request's latency (an 8B embedder on a 6 GB GPU takes noticeable time per
# text) so a large seed/re-embed batch cannot outlive the HTTP timeout.
_OLLAMA_EMBED_BATCH_MAX = 32


class OllamaEmbeddingAdapter:
    """Calls Ollama's /api/embed endpoint (batched `input`, note: NOT the
    legacy single-input /api/embeddings).

    The modern endpoint matters for two reasons beyond batching:
      - It honours a `dimensions` field for MRL-trained models
        (qwen3-embedding: native 4096 → server-side truncate+renormalize to
        the requested width; probed live 2026-07-11). The legacy endpoint
        always returns the native dimension.
      - Batched input amortises per-request overhead for seeding/re-embeds.

    Prefixes implement task-aware asymmetric retrieval (applied AFTER the
    char-limit truncation so the task marker is never cut off):
      - ``query_prefix`` — queries only (qwen3-embedding's instruction,
        nomic's "search_query: "). Empty for symmetric models.
      - ``document_prefix`` — passages only (nomic v1.5/v2-moe train with
        "search_document: "). Empty = raw passages (backward compatible).

    ``num_ctx`` forwards Ollama's per-request options.num_ctx so embedding
    models run at their real context window (Ollama otherwise truncates at
    the loaded default). ``max_input_chars`` hard-caps each input before
    embedding — guards small-window models like v2-moe (512 tokens).
    """

    def __init__(
        self,
        base_url: str,
        model: str = "nomic-embed-text",
        *,
        dimension: int = 768,
        timeout: float = 60.0,
        request_dimensions: int = 0,
        query_prefix: str = "",
        document_prefix: str = "",
        num_ctx: int = 0,
        max_input_chars: int = 0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dimension = dimension
        self._timeout = timeout
        self._request_dimensions = request_dimensions
        self._query_prefix = query_prefix
        self._document_prefix = document_prefix
        self._num_ctx = num_ctx
        self._max_input_chars = max_input_chars
        # Injectable for hermetic tests (httpx.MockTransport); None = real HTTP.
        self._transport = transport

    @property
    def model_id(self) -> str:
        return f"ollama:{self._model}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def _prepare(self, text: str, prefix: str) -> str:
        """Truncate to the char cap, then prepend the task prefix."""
        if self._max_input_chars > 0:
            text = text[: self._max_input_chars]
        return prefix + text

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed PASSAGES — the document prefix (when configured) applies."""
        return await self._embed_raw([self._prepare(text, self._document_prefix) for text in texts])

    async def embed_query(self, query: str) -> list[float]:
        """Embed a query, applying the instruction prefix when configured."""
        [vector] = await self._embed_raw([self._prepare(query, self._query_prefix)])
        return vector

    async def _embed_raw(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            vectors: list[list[float]] = []
            for start in range(0, len(texts), _OLLAMA_EMBED_BATCH_MAX):
                batch = texts[start : start + _OLLAMA_EMBED_BATCH_MAX]
                body: dict[str, Any] = {"model": self._model, "input": batch}
                if self._request_dimensions > 0:
                    body["dimensions"] = self._request_dimensions
                if self._num_ctx > 0:
                    body["options"] = {"num_ctx": self._num_ctx}
                response = await client.post(f"{self._base_url}/api/embed", json=body)
                response.raise_for_status()
                payload = response.json()
                batch_vectors = payload["embeddings"]
                if len(batch_vectors) != len(batch):
                    raise ValueError(
                        f"Ollama returned {len(batch_vectors)} embeddings for a "
                        f"batch of {len(batch)} inputs."
                    )
                for vector in batch_vectors:
                    if len(vector) != self._dimension:
                        raise ValueError(
                            f"Ollama returned dim={len(vector)} for {self._model!r}, "
                            f"expected {self._dimension}. Either set "
                            f"EMBEDDING_REQUEST_DIMENSIONS={self._dimension} (MRL-"
                            "trained models only — e.g. qwen3-embedding, native "
                            "4096), or set EMBEDDING_DIMENSION to the model's "
                            "native dimension and reconcile the DB via "
                            "`python -m wolf_server.management.embedding_schema "
                            "--apply`."
                        )
                    vectors.append(vector)
            return vectors


class SentenceTransformersEmbeddingAdapter:
    """In-process embedding via the HuggingFace sentence-transformers library.

    Requires the optional `embeddings-local` extra. Loads the model once at
    construction and keeps it resident. Runs on GPU when available, falls
    back to CPU otherwise.

    BGE-family models (e.g. `BAAI/bge-base-en-v1.5`) use **asymmetric
    retrieval**: queries should be prefixed with
    `"Represent this sentence for searching relevant passages: "` while
    passages are embedded raw. This adapter exposes `embed_query(...)` and
    `embed(...)` (the latter for passages) so callers can choose.

    Async-correctness note: sentence-transformers is synchronous. We wrap
    encode calls in `asyncio.to_thread` so they don't block the event loop.
    """

    BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

    def __init__(
        self,
        model_name: str,
        *,
        dimension: int = 768,
        request_dimensions: int = 0,
        query_prefix: str = "",
        document_prefix: str = "",
        max_input_chars: int = 0,
    ) -> None:
        # Lazy import — keeps the module importable when the optional extra
        # isn't installed. A clear error surface lands in the constructor
        # rather than at module load.
        try:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is not installed. Either "
                "`uv sync --extra embeddings-local` to install the optional "
                "extra, or set EMBEDDING_PROVIDER=ollama in .env to use the "
                "default Ollama-hosted adapter."
            ) from exc

        # Device selection: prefer CUDA, fall back to CPU. Avoid the
        # `device='auto'` shorthand because older sentence-transformers
        # releases don't accept it.
        try:
            import torch  # noqa: PLC0415

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"

        self._model_name = model_name
        self._dimension = dimension
        self._query_prefix = query_prefix
        self._document_prefix = document_prefix
        self._max_input_chars = max_input_chars
        # MRL truncation via the library's own truncate_dim (the same
        # officially supported truncate+renormalize the Ollama path requests
        # server-side). 0 = model-native output.
        self._model: Any = (
            SentenceTransformer(model_name, device=device, truncate_dim=request_dimensions)
            if request_dimensions > 0
            else SentenceTransformer(model_name, device=device)
        )
        self._device = device

    @property
    def model_id(self) -> str:
        return f"st:{self._model_name}"

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def device(self) -> str:
        return self._device

    def _truncate(self, text: str) -> str:
        if self._max_input_chars > 0:
            return text[: self._max_input_chars]
        return text

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed passages — the document prefix (when configured) applies,
        after the char-limit truncation so the task marker survives."""
        return await self._encode([self._document_prefix + self._truncate(text) for text in texts])

    async def embed_query(self, query: str) -> list[float]:
        """Embed a query — an explicit configured prefix wins; otherwise the
        BGE asymmetric prefix is applied automatically for BGE models."""
        if self._query_prefix:
            prepared = self._query_prefix + self._truncate(query)
        else:
            is_bge = "bge" in self._model_name.lower()
            truncated = self._truncate(query)
            prepared = self.BGE_QUERY_PREFIX + truncated if is_bge else truncated
        vectors = await self._encode([prepared])
        return vectors[0]

    async def _encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # encode() is synchronous and CPU/GPU-bound — offload to a worker
        # thread so the event loop stays responsive.
        result = await asyncio.to_thread(
            self._model.encode,
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        vectors = [v.tolist() for v in result]
        # Surface dimension mismatch loudly rather than silently writing
        # wrong-width rows into the DB.
        if vectors and len(vectors[0]) != self._dimension:
            raise ValueError(
                f"sentence-transformers model {self._model_name!r} returned "
                f"dim={len(vectors[0])}, expected {self._dimension}. The "
                f"DB column is locked at {self._dimension}; pick a "
                f"matching-dim model or run a re-embedding migration."
            )
        return vectors


def _build_provider(
    provider_name: str,
    model_id: str,
    settings: "Settings",
    *,
    dimension: int,
    request_dimensions: int,
    query_prefix: str,
    document_prefix: str,
    num_ctx: int,
    max_input_chars: int,
) -> EmbeddingProvider:
    """Shared factory body — builds an EmbeddingProvider from a name + model.

    Pulled out so the primary and auxiliary factories share the same
    branch logic (and the same future runtimes). EVERY knob is
    per-embedder (ADR 0033): the primary and aux each carry their own
    dimension, MRL truncation, prefixes, context window, and char cap —
    e.g. a 4096-dim qwen3-embedding primary next to a 768-dim v2-moe aux.
    """
    name = provider_name.lower()
    if name == "ollama":
        return OllamaEmbeddingAdapter(
            settings.ollama_base_url,
            model=model_id,
            dimension=dimension,
            request_dimensions=request_dimensions,
            query_prefix=query_prefix,
            document_prefix=document_prefix,
            num_ctx=num_ctx,
            max_input_chars=max_input_chars,
        )
    if name in {"sentence-transformers", "st", "sentence_transformers"}:
        return SentenceTransformersEmbeddingAdapter(
            model_id,
            dimension=dimension,
            request_dimensions=request_dimensions,
            query_prefix=query_prefix,
            document_prefix=document_prefix,
            max_input_chars=max_input_chars,
        )
    raise ValueError(
        f"Unknown embedding_provider {provider_name!r}; expected 'ollama' or "
        f"'sentence-transformers'."
    )


def make_embedding_provider(settings: "Settings") -> EmbeddingProvider:
    """Construct the primary EmbeddingProvider.

    Provider selection is env-driven via `EMBEDDING_PROVIDER` to keep the
    swap reversible without code changes. The sentence-transformers path
    requires `uv sync --extra embeddings-local` (torch is not a default
    runtime dep per ADR 0007).
    """
    return _build_provider(
        settings.embedding_provider,
        settings.embedding_model,
        settings,
        dimension=settings.embedding_dimension,
        request_dimensions=settings.embedding_request_dimensions,
        query_prefix=settings.embedding_query_prefix,
        document_prefix=settings.embedding_document_prefix,
        num_ctx=settings.embedding_num_ctx,
        max_input_chars=settings.embedding_char_limit,
    )


def make_embedding_provider_aux(
    settings: "Settings",
) -> EmbeddingProvider | None:
    """Construct the optional secondary EmbeddingProvider (ADR 0014).

    Returns `None` when `EMBEDDING_MODEL_AUX` is empty — i.e. when the
    operator hasn't configured multi-embedding retrieval. The store then
    behaves exactly as before (BM25 + single vector leg). When set, the
    second embedder feeds the third RRF leg.
    """
    if not settings.embedding_model_aux:
        return None
    provider_name = settings.embedding_provider_aux or settings.embedding_provider
    return _build_provider(
        provider_name,
        settings.embedding_model_aux,
        settings,
        dimension=settings.embedding_dimension_aux or settings.embedding_dimension,
        request_dimensions=settings.embedding_request_dimensions_aux,
        query_prefix=settings.embedding_query_prefix_aux,
        document_prefix=settings.embedding_document_prefix_aux,
        num_ctx=settings.embedding_num_ctx_aux,
        max_input_chars=settings.embedding_char_limit_aux,
    )
