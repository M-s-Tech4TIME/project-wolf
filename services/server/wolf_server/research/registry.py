"""Resolve a `SearchProvider` from config (ADR 0032, slice 6-f.1).

The web-research analogue of `agent/model_resolver.py`: picks the backend
from process-wide settings today; the OrganizationContext parameter is
accepted (and reserved) so per-organization backend selection — an org
choosing Brave/Tavily with its own key — can be added without changing call
sites (same seam as per-org model config, ADR 0031).
"""

import structlog
from wolf_secrets.interface import SecretsBackend

from wolf_server.config import Settings
from wolf_server.organization.context import OrganizationContext
from wolf_server.research.interface import (
    SearchProvider,
    SearchProviderUnconfiguredError,
)
from wolf_server.research.searxng import SearxngProvider

logger = structlog.get_logger(__name__)

# Hosted backends are ADR 0032 out-of-scope until the SearXNG default path is
# proven — named here so the error message can distinguish "not yet wired"
# from "no such provider".
_DEFERRED_PROVIDERS = frozenset({"brave", "tavily"})


async def get_search_provider_for_organization(
    _ctx: OrganizationContext,
    settings: Settings,
    _secrets: SecretsBackend,
) -> SearchProvider:
    """Return the configured search backend for a request.

    Fails closed: raises :class:`SearchProviderUnconfiguredError` when web
    research is disabled (`WEB_SEARCH_ENABLED=0`, the default) — callers gate
    tool *registration* on the flag, so reaching this while disabled is a
    wiring bug, not a user error. `_secrets` is reserved for the hosted
    backends' API keys (`search.brave.api_key` / `search.tavily.api_key`).
    """
    if not settings.web_search_enabled:
        raise SearchProviderUnconfiguredError(
            "Web research is disabled (set WEB_SEARCH_ENABLED=1 to enable)"
        )

    provider = settings.web_search_provider.strip().lower()
    match provider:
        case "searxng":
            return SearxngProvider(base_url=settings.searxng_url)
        case _ if provider in _DEFERRED_PROVIDERS:
            raise SearchProviderUnconfiguredError(
                f"Search provider {provider!r} is not wired yet — hosted backends "
                f"land after the SearXNG default is proven (ADR 0032 out-of-scope); "
                f"set WEB_SEARCH_PROVIDER=searxng"
            )
        case _:
            raise SearchProviderUnconfiguredError(
                f"Unknown search provider: {provider!r} (expected 'searxng')"
            )
