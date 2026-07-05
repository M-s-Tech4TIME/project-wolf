"""Web-research layer — provider-agnostic search behind a pluggable adapter.

ADR 0032. Mirrors the model-provider abstraction (`wolf_server.models` +
`agent/model_resolver.py`): a `SearchProvider` protocol, concrete adapters
(SearXNG self-hosted = the free default), and a resolver that picks the
provider from config with the per-organization seam reserved (6-f.1).

Slice 6-f.3 added the live request path: the SSRF-guarded fetcher
(`fetcher`/`weburl`/`extract`), the docs-first policy (`policy`), the
bounded crawler (`crawl`), and the per-request `ResearchContext`
(`context`) consumed by the `web_search` / `web_fetch` / `web_crawl` tools
in `wolf_server.tools.web_research` — all opt-in via `WEB_SEARCH_ENABLED`.
"""

from wolf_server.research.context import ResearchContext, open_research_context
from wolf_server.research.fetcher import WebFetcher, WebFetchError
from wolf_server.research.interface import (
    SearchProvider,
    SearchProviderError,
    SearchProviderUnconfiguredError,
    SearchResult,
)
from wolf_server.research.registry import get_search_provider_for_organization
from wolf_server.research.searxng import SearxngProvider
from wolf_server.research.weburl import WebUrlError

__all__ = [
    "ResearchContext",
    "SearchProvider",
    "SearchProviderError",
    "SearchProviderUnconfiguredError",
    "SearchResult",
    "SearxngProvider",
    "WebFetchError",
    "WebFetcher",
    "WebUrlError",
    "get_search_provider_for_organization",
    "open_research_context",
]
