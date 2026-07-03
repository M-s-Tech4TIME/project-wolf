"""SearchProvider protocol and result model (ADR 0032, slice 6-f.1).

The web-research analogue of `models/interface.py`: every search backend
(SearXNG self-hosted default; Brave / Tavily hosted options later) satisfies
the same protocol, so the `web_search` tool (slice 6-f.3) and the resolver
never know which backend is active.

Deliberately NOT on this protocol: page fetching. The fetcher is
provider-independent (ADR 0032 A2) — one SSRF-guarded HTTP fetch shared by
every backend — and lands with the tools in 6-f.3.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field
from wolf_common.errors import WolfError


class SearchProviderError(WolfError):
    """The search backend failed (unreachable, non-2xx, malformed payload).

    Callers degrade gracefully — a broken backend must never hang or break
    the chat stream (ADR 0032 A6 §14).
    """

    http_status = 502
    error_code = "search_provider_error"


class SearchProviderUnconfiguredError(WolfError):
    """Web research is disabled or the configured provider is unusable."""

    http_status = 500
    error_code = "search_provider_unconfigured"


class SearchResult(BaseModel):
    """One ranked hit from a web search — the provider-agnostic shape.

    Every backend normalizes into this; it is what the `web_search` tool
    returns to the model and what a `Citation` points back to.
    """

    url: str = Field(description="Absolute URL of the hit")
    title: str = Field(description="Result title")
    snippet: str = Field(default="", description="Short content excerpt")
    engine: str = Field(
        default="",
        description="Upstream engine that produced the hit (e.g. 'duckduckgo'); "
        "empty when the backend does not report one",
    )
    published: str | None = Field(
        default=None,
        description="Publication date as reported by the backend, if any",
    )


@runtime_checkable
class SearchProvider(Protocol):
    """Adapter contract every search backend implementation must satisfy."""

    # Backend name for citations / audit (e.g. "searxng", "brave").
    name: str

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        """Run one search and return up to ``max_results`` normalized hits.

        Raises :class:`SearchProviderError` on backend failure — never
        returns partial garbage silently.
        """
        ...
