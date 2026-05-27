"""Embedding provider abstraction + Ollama-hosted + in-process implementations.

The protocol hides the runtime (Ollama HTTP, sentence-transformers in-process,
fastembed/ONNX tomorrow). The contract is: embed a batch of texts, return a
list of fixed-dimension vectors in the same order. Dimension is reported via
`dimension` and must equal `app.knowledge.models.EMBEDDING_DIMENSION` for
the active adapter.

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
    from app.config import Settings


class EmbeddingProvider(Protocol):
    """Returns fixed-dimension vectors for a batch of input texts."""

    @property
    def model_id(self) -> str:
        """Stable identifier stamped on every chunk this provider embeds."""

    @property
    def dimension(self) -> int:
        """Vector dimension; must match the DB column width."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Output order matches input order."""


class OllamaEmbeddingAdapter:
    """Calls Ollama's /api/embeddings endpoint per text in the batch.

    Ollama's embedding endpoint is single-input; batching is sequential on
    the client side. For Phase 3 dev workloads this is fine — a ~10-chunk
    seed takes well under a second on the RTX 4050. If batch sizes ever
    grow into the thousands, swap to a /api/embed (note: plural) endpoint
    once Ollama exposes one, or move to a true batching adapter.
    """

    def __init__(
        self,
        base_url: str,
        model: str = "nomic-embed-text",
        *,
        dimension: int = 768,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dimension = dimension
        self._timeout = timeout

    @property
    def model_id(self) -> str:
        return f"ollama:{self._model}"

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            vectors: list[list[float]] = []
            for text in texts:
                response = await client.post(
                    f"{self._base_url}/api/embeddings",
                    json={"model": self._model, "prompt": text},
                )
                response.raise_for_status()
                payload = response.json()
                vector = payload["embedding"]
                if len(vector) != self._dimension:
                    raise ValueError(
                        f"Ollama returned dim={len(vector)}, expected "
                        f"{self._dimension}. Adjust EMBEDDING_DIMENSION or "
                        f"pick a different model."
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

    def __init__(self, model_name: str, *, dimension: int = 768) -> None:
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
        self._model: Any = SentenceTransformer(model_name, device=device)
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

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed passages (no query prefix)."""
        return await self._encode(texts)

    async def embed_query(self, query: str) -> list[float]:
        """Embed a query (BGE asymmetric prefix applied automatically)."""
        is_bge = "bge" in self._model_name.lower()
        prepared = self.BGE_QUERY_PREFIX + query if is_bge else query
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
) -> EmbeddingProvider:
    """Shared factory body — builds an EmbeddingProvider from a name + model.

    Pulled out so the primary and auxiliary factories share the same
    branch logic (and the same future runtimes).
    """
    name = provider_name.lower()
    if name == "ollama":
        return OllamaEmbeddingAdapter(
            settings.ollama_base_url,
            model=model_id,
            dimension=settings.embedding_dimension,
        )
    if name in {"sentence-transformers", "st", "sentence_transformers"}:
        return SentenceTransformersEmbeddingAdapter(
            model_id,
            dimension=settings.embedding_dimension,
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
        settings.embedding_provider, settings.embedding_model, settings
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
    return _build_provider(provider_name, settings.embedding_model_aux, settings)
