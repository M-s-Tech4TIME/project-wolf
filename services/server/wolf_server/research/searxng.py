"""SearXNG adapter — Wolf's free, self-hosted default search backend.

ADR 0032 (slice 6-f.1). Talks to the `wolf-search` component over
``GET {base}/search?q=…&format=json``. In every recommended topology that is
a loopback URL (wolf-search is wolf-server's sidecar, ADR 0032 A3.1); a
dedicated search tier swaps the base URL for an mTLS-fronted host — this
adapter only ever sees ``SEARXNG_URL``.

Security posture here (the rest lands with the tools in 6-f.3):
- The response JSON is **schema-validated** (ADR 0032 A6 §4) — a malformed
  or hostile payload raises :class:`SearchProviderError`, it is never
  passed through shape-unchecked. Entries missing ``url``/``title`` are
  dropped, not guessed at.
- The parse shape below matches SearXNG's documented JSON format; slice
  6-f.2 stands up the real instance and re-verifies it empirically
  (scope-and-validation discipline) before the tools consume it.
"""

from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field, ValidationError

from wolf_server.research.interface import SearchProviderError, SearchResult

logger = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 15.0


class _SearxngEntry(BaseModel):
    """One raw result entry as SearXNG emits it — validated, never trusted."""

    url: str
    title: str
    content: str = ""
    engine: str = ""
    # SearXNG emits `publishedDate` (camelCase), often null.
    published_date: str | None = Field(default=None, alias="publishedDate")


class _SearxngResponse(BaseModel):
    """Top-level shape of ``GET /search?format=json`` — the part Wolf reads."""

    results: list[dict[str, Any]] = Field(default_factory=list)


class SearxngProvider:
    """`SearchProvider` implementation backed by a SearXNG instance."""

    name = "searxng"

    def __init__(
        self,
        *,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        # Injectable client so tests stub the HTTP boundary (httpx.MockTransport)
        # — same pattern as OllamaAdapter; hermetic CI, no live SearXNG.
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        """Run one search against SearXNG and normalize the hits."""
        try:
            response = await self._client.get(
                f"{self._base_url}/search",
                params={"q": query, "format": "json"},
            )
        except httpx.HTTPError as exc:
            raise SearchProviderError(f"SearXNG unreachable at {self._base_url}: {exc}") from exc

        if response.status_code != 200:
            raise SearchProviderError(
                f"SearXNG returned HTTP {response.status_code} "
                f"(is 'json' enabled under search.formats in settings.yml?)"
            )

        try:
            payload = _SearxngResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise SearchProviderError(f"SearXNG returned a malformed payload: {exc}") from exc

        hits: list[SearchResult] = []
        dropped = 0
        for raw in payload.results:
            try:
                entry = _SearxngEntry.model_validate(raw)
            except ValidationError:
                dropped += 1  # missing/invalid url or title — never guess
                continue
            hits.append(
                SearchResult(
                    url=entry.url,
                    title=entry.title,
                    snippet=entry.content,
                    engine=entry.engine,
                    published=entry.published_date,
                )
            )
            if len(hits) >= max_results:
                break

        if dropped:
            logger.warning("searxng_entries_dropped", dropped=dropped, query_len=len(query))
        return hits
