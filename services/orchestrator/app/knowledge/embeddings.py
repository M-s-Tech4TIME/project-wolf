"""Embedding provider abstraction + Ollama-hosted implementation.

The protocol hides the runtime (Ollama HTTP today, sentence-transformers or
fastembed tomorrow). The contract is: embed a batch of texts, return a list
of fixed-dimension vectors in the same order. Dimension is reported via
`dimension` and must equal `app.knowledge.models.EMBEDDING_DIMENSION` for
the active adapter.

Per doc 06, "tie chunk records to embedding-model identity, so changing the
embedding model triggers a planned re-embedding rather than silent
inconsistency." `model_id` is returned alongside vectors and stamped on
every KnowledgeChunk row.
"""

from typing import Protocol

import httpx


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
