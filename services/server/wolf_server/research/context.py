"""Per-request research context — provider + fetcher + budget (ADR 0032 A6 §5/§9).

One `ResearchContext` is built per chat request (mirroring the per-request
model/judge/DB isolation of `grounding-concurrency-model`): the search
provider, the shared guarded fetcher, the config snapshot, and the request's
combined tool-call budget all live here, so one request's research can never
bleed into — or starve — another's (MSSP isolation).

Budget semantics: every `web_search` / `web_fetch` / `web_crawl` CALL costs
one unit from `web_search_budget_per_request` (the `max_uses` analog of
Claude's web tools). Exhaustion raises `GuardrailViolation`, which the
dispatcher already converts into a clean, audited tool error the model can
act on — degrade to answering from evidence in hand, never a hang.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import structlog
from wolf_secrets.interface import SecretsBackend

from wolf_server.config import Settings
from wolf_server.guardrails.limits import GuardrailViolation
from wolf_server.organization.context import OrganizationContext
from wolf_server.research.fetcher import WebFetcher
from wolf_server.research.interface import SearchProvider
from wolf_server.research.registry import get_search_provider_for_organization

logger = structlog.get_logger(__name__)


@dataclass
class ResearchContext:
    """Everything the three web tools need for one chat request."""

    provider: SearchProvider
    fetcher: WebFetcher
    # Config snapshot (ADR 0032 A7) — tools read these, never Settings.
    max_results: int
    crawl_max_depth: int
    crawl_max_pages: int
    crawl_per_host_delay: float
    budget_remaining: int

    def consume_budget(self, tool_name: str) -> None:
        """Take one unit of the per-request web budget or refuse cleanly."""
        if self.budget_remaining <= 0:
            raise GuardrailViolation(
                f"{tool_name} refused: the per-request web-research budget is "
                "exhausted. Answer from the evidence you already gathered; if "
                "something essential is missing, say what it is."
            )
        self.budget_remaining -= 1


@asynccontextmanager
async def open_research_context(
    ctx: OrganizationContext,
    settings: Settings,
    secrets: SecretsBackend,
) -> AsyncIterator[ResearchContext | None]:
    """Yield a per-request ResearchContext, or None when web research is off.

    Owns the fetcher's (and provider's) HTTP client lifecycle — both are
    closed when the request finishes.
    """
    if not settings.web_search_enabled:
        yield None
        return

    provider = await get_search_provider_for_organization(ctx, settings, secrets)
    fetcher = WebFetcher(
        max_bytes=settings.web_fetch_max_bytes,
        timeout_seconds=settings.web_fetch_timeout_seconds,
    )
    try:
        yield ResearchContext(
            provider=provider,
            fetcher=fetcher,
            max_results=settings.web_search_max_results,
            crawl_max_depth=settings.web_crawl_max_depth,
            crawl_max_pages=settings.web_crawl_max_pages,
            crawl_per_host_delay=settings.web_crawl_per_host_rate,
            budget_remaining=settings.web_search_budget_per_request,
        )
    finally:
        await fetcher.aclose()
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:
            await aclose()
