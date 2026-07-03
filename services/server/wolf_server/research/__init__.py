"""Web-research layer — provider-agnostic search behind a pluggable adapter.

ADR 0032 (slice 6-f.1). Mirrors the model-provider abstraction
(`wolf_server.models` + `agent/model_resolver.py`): a `SearchProvider`
protocol, concrete adapters (SearXNG self-hosted = the free default), and a
resolver that picks the provider from config with the per-organization seam
reserved.

INERT at runtime until slice 6-f.3 registers the `web_search` / `web_fetch` /
`web_crawl` tools — nothing imports this package in the request path yet.
"""

from wolf_server.research.interface import (
    SearchProvider,
    SearchProviderError,
    SearchProviderUnconfiguredError,
    SearchResult,
)
from wolf_server.research.registry import get_search_provider_for_organization
from wolf_server.research.searxng import SearxngProvider

__all__ = [
    "SearchProvider",
    "SearchProviderError",
    "SearchProviderUnconfiguredError",
    "SearchResult",
    "SearxngProvider",
    "get_search_provider_for_organization",
]
