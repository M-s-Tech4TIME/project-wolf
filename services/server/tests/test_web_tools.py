"""web_search / web_fetch / web_crawl tools + registration gating (6-f.3).

Hermetic: the search provider is a stub, the fetcher rides MockTransport,
DNS is a fake resolver. Pins the tool contract — docs-first ranking + per-
result citations, the untrusted-content envelope, budget enforcement, cap
clamping, graceful degradation (`ToolDegradedError`) — plus the opt-in
registration gate and the conditional system-prompt suffix.
"""

import uuid
from typing import Any

import httpx
import pytest
from wolf_server.config import Settings
from wolf_server.guardrails.limits import DEFAULT_LIMITS, GuardrailViolation
from wolf_server.organization.context import OrganizationContext
from wolf_server.research.context import ResearchContext
from wolf_server.research.fetcher import WebFetcher
from wolf_server.research.interface import SearchProviderError, SearchResult
from wolf_server.tools.base import ToolDegradedError, ToolExecContext
from wolf_server.tools.web_research import (
    WebCrawlOutput,
    WebCrawlTool,
    WebFetchOutput,
    WebFetchTool,
    WebSearchInput,
    WebSearchOutput,
    WebSearchTool,
)

_IPS = {"docs.example.com": "93.184.216.34", "example.com": "93.184.216.40"}


async def _resolver(host: str, port: int) -> list[str]:
    return [_IPS[host]]


class _StubProvider:
    name = "stub"

    def __init__(
        self, results: list[SearchResult] | None = None, error: Exception | None = None
    ) -> None:
        self._results = results or []
        self._error = error
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        self.calls.append((query, max_results))
        if self._error is not None:
            raise self._error
        return self._results[:max_results]


def _handler(request: httpx.Request) -> httpx.Response:
    host = request.headers["host"]
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(200, content=b"User-agent: *\nAllow: /\n")
    if path == "/sitemap.xml":
        return httpx.Response(404, content=b"")
    body = (
        f"<html><head><title>Page {path}</title></head>"
        f'<body>Content of {host}{path}. <a href="/linked">link</a></body></html>'
    )
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/html"})


def _research(
    provider: Any = None,
    *,
    budget: int = 5,
    crawl_max_depth: int = 1,
    crawl_max_pages: int = 3,
) -> ResearchContext:
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler), timeout=5.0)
    fetcher = WebFetcher(max_bytes=50_000, timeout_seconds=5.0, client=client, resolver=_resolver)
    return ResearchContext(
        provider=provider or _StubProvider(),
        fetcher=fetcher,
        max_results=5,
        crawl_max_depth=crawl_max_depth,
        crawl_max_pages=crawl_max_pages,
        crawl_per_host_delay=0.0,
        budget_remaining=budget,
    )


def _exec_ctx(research: ResearchContext | None) -> ToolExecContext:
    return ToolExecContext(
        organization=OrganizationContext(
            organization_id=uuid.uuid4(),
            organization_slug="acme",
            user_id=uuid.uuid4(),
            user_email="analyst@example.com",
            role="analyst",
            session_id="sess-web",
        ),
        limits=DEFAULT_LIMITS,
        opensearch=None,
        server_api=None,
        research=research,
    )


# ── web_search ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_web_search_ranks_docs_first_with_per_result_citations() -> None:
    provider = _StubProvider(
        [
            SearchResult(url="https://stackoverflow.com/q/1", title="SO answer"),
            SearchResult(url="https://documentation.wazuh.com/sca", title="SCA docs"),
        ]
    )
    research = _research(provider)
    out = await WebSearchTool().run(_exec_ctx(research), WebSearchInput(query="wazuh sca policy"))
    assert isinstance(out, WebSearchOutput)
    # Official docs float above the community hit (A4).
    assert [r.source for r in out.results] == ["official_docs", "community"]
    assert out.results[0].title == "SCA docs"
    # One citation per result, each carrying url/title/source (A1/A5).
    assert len(out.citations) == 2
    assert out.citations[0].url == "https://documentation.wazuh.com/sca"
    assert out.citations[0].source == "official_docs"
    assert out.citations[0].tool == "web_search"
    assert "1 from official" in out.summary
    # Budget consumed once.
    assert research.budget_remaining == 4
    # The provider got the configured default max_results.
    assert provider.calls == [("wazuh sca policy", 5)]


@pytest.mark.asyncio
async def test_web_search_backend_failure_degrades_cleanly() -> None:
    provider = _StubProvider(error=SearchProviderError("SearXNG unreachable at ..."))
    with pytest.raises(ToolDegradedError, match="unavailable"):
        await WebSearchTool().run(_exec_ctx(_research(provider)), WebSearchInput(query="wazuh"))


@pytest.mark.asyncio
async def test_budget_exhaustion_is_a_guardrail_refusal() -> None:
    research = _research(budget=0)
    with pytest.raises(GuardrailViolation, match="budget"):
        await WebSearchTool().run(_exec_ctx(research), WebSearchInput(query="wazuh"))


@pytest.mark.asyncio
async def test_missing_research_context_degrades_not_crashes() -> None:
    with pytest.raises(ToolDegradedError, match="not configured"):
        await WebSearchTool().run(_exec_ctx(None), WebSearchInput(query="wazuh"))


# ── web_fetch ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_web_fetch_envelopes_content_and_cites() -> None:
    research = _research()
    tool = WebFetchTool()
    out = await tool.run(_exec_ctx(research), tool.InputModel(url="https://docs.example.com/guide"))
    assert isinstance(out, WebFetchOutput)
    # Untrusted-content envelope (A6 §2) wraps the page text.
    assert out.content.startswith("[BEGIN UNTRUSTED WEB CONTENT")
    assert out.content.rstrip().endswith("[END UNTRUSTED WEB CONTENT]")
    assert "Content of docs.example.com/guide" in out.content
    assert out.title == "Page /guide"
    assert out.source == "community"
    assert out.citation.url == "https://docs.example.com/guide"
    assert out.citation.tool == "web_fetch"
    assert research.budget_remaining == 4


@pytest.mark.asyncio
async def test_web_fetch_ssrf_rejection_degrades_cleanly() -> None:
    tool = WebFetchTool()
    with pytest.raises(ToolDegradedError, match="Could not fetch"):
        await tool.run(_exec_ctx(_research()), tool.InputModel(url="https://user:pw@example.com/"))


# ── web_crawl ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_web_crawl_returns_pages_with_citations() -> None:
    research = _research(crawl_max_depth=1, crawl_max_pages=2)
    tool = WebCrawlTool()
    out = await tool.run(
        _exec_ctx(research),
        tool.InputModel(url="https://docs.example.com/", query="linked content"),
    )
    assert isinstance(out, WebCrawlOutput)
    assert [p.url for p in out.pages] == [
        "https://docs.example.com/",
        "https://docs.example.com/linked",
    ]
    assert all(p.excerpt.startswith("[BEGIN UNTRUSTED WEB CONTENT") for p in out.pages)
    assert len(out.citations) == 2
    assert out.citations[1].url == "https://docs.example.com/linked"
    assert "Crawled 2 pages" in out.summary


@pytest.mark.asyncio
async def test_web_crawl_model_cannot_widen_server_caps() -> None:
    research = _research(crawl_max_depth=1, crawl_max_pages=1)
    tool = WebCrawlTool()
    out = await tool.run(
        _exec_ctx(research),
        # The model asks for far more than the server allows.
        tool.InputModel(url="https://docs.example.com/", query="xy", max_pages=40, max_depth=3),
    )
    assert isinstance(out, WebCrawlOutput)
    assert len(out.pages) == 1  # server cap wins


# ── Registration gating + prompt suffix ──────────────────────────────────────


@pytest.fixture
def isolated_registries() -> Any:
    from wolf_server.models.registry import registry as schema_registry
    from wolf_server.tools.registry import runtime_registry

    schema_registry.clear()
    runtime_registry.clear()
    yield
    schema_registry.clear()
    runtime_registry.clear()


def test_web_tools_not_registered_by_default(isolated_registries: Any) -> None:
    from wolf_server.tools.registration import register_all_read_tools
    from wolf_server.tools.registry import runtime_registry

    register_all_read_tools(Settings(web_search_enabled=False))
    names = set(runtime_registry.names())
    assert {"web_search", "web_fetch", "web_crawl"} & names == set()


def test_web_tools_registered_when_enabled(isolated_registries: Any) -> None:
    from wolf_server.tools.registration import register_all_read_tools
    from wolf_server.tools.registry import runtime_registry

    register_all_read_tools(Settings(web_search_enabled=True))
    names = set(runtime_registry.names())
    assert {"web_search", "web_fetch", "web_crawl"} <= names


def test_prompt_suffix_teaches_the_three_tools() -> None:
    from wolf_server.agent.prompts import WEB_RESEARCH_SUFFIX

    for token in ("web_search", "web_fetch", "web_crawl", "UNTRUSTED", "GENERIC"):
        assert token in WEB_RESEARCH_SUFFIX


def test_prompt_suffix_teaches_research_to_act() -> None:
    # 6-f.4 (the universal-power directive): research is a step in DOING, not
    # just answering — the suffix must chain research into the propose flow
    # without weakening the approval posture.
    from wolf_server.agent.prompts import WEB_RESEARCH_SUFFIX

    for token in ("RESEARCH-TO-ACT", "propose_config_change", "approval"):
        assert token in WEB_RESEARCH_SUFFIX


def test_system_prompt_teaches_the_config_authoring_loop() -> None:
    # 6-f.4 (ADR 0032 B1): preview → show the analyst the diff → confirm →
    # propose; plus the generalized ops and the blocked-section rail.
    from wolf_server.agent.prompts import SYSTEM_PROMPT

    for token in (
        "THE AUTHORING LOOP",
        "needs_confirmation",
        "user_confirmed=true",
        "upsert_block",
        "remove_block",
        "block_key",
        "cluster/auth/indexer/ruleset",
    ):
        assert token in SYSTEM_PROMPT
