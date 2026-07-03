"""Web-research scaffolding — SearchProvider protocol, SearXNG adapter, resolver.

ADR 0032 (slice 6-f.1). The SearXNG HTTP boundary is stubbed with an httpx
MockTransport (no live instance — hermetic CI, same pattern as the Ollama /
OpenRouter adapter tests). Pins the load-bearing behaviour:
  - the adapter normalizes SearXNG's documented JSON shape into SearchResults
    (publishedDate alias, null tolerated, snippet/engine mapped);
  - the payload is schema-validated (A6 §4): malformed top-level shapes raise,
    entries missing url/title are DROPPED (never guessed), valid ones survive;
  - non-200 / unreachable → SearchProviderError (graceful degradation, A6 §14);
  - results are capped at max_results;
  - the resolver fails closed while disabled (the WEB_SEARCH_ENABLED=0
    default), builds a SearxngProvider from settings when enabled, and
    distinguishes deferred hosted backends (brave/tavily) from unknown ones.
"""

import json
import uuid
from typing import Any

import httpx
import pytest
from wolf_server.config import Settings
from wolf_server.organization.context import OrganizationContext
from wolf_server.research.interface import (
    SearchProvider,
    SearchProviderError,
    SearchProviderUnconfiguredError,
)
from wolf_server.research.registry import get_search_provider_for_organization
from wolf_server.research.searxng import SearxngProvider

_BASE = "http://127.0.0.1:1307"  # the operator-chosen wolf-search port (6-f.2)

# A realistic SearXNG /search?format=json payload (documented shape; slice
# 6-f.2 re-verifies it against the live instance before the tools consume it).
_PAYLOAD: dict[str, Any] = {
    "query": "wazuh sca configuration",
    "number_of_results": 3,
    "results": [
        {
            "url": "https://documentation.wazuh.com/current/user-manual/capabilities/sec-config-assessment/index.html",
            "title": "Security Configuration Assessment - Wazuh documentation",
            "content": "The SCA module performs scans to discover misconfigurations.",
            "engine": "duckduckgo",
            "publishedDate": None,
            "score": 2.0,
            "category": "general",
        },
        {
            "url": "https://wazuh.com/blog/security-configuration-assessment/",
            "title": "SCA in depth",
            "content": "Blog walkthrough.",
            "engine": "brave",
            "publishedDate": "2024-03-01T00:00:00",
        },
        {
            "url": "https://example.com/third",
            "title": "Community post",
        },
    ],
    "answers": [],
    "corrections": [],
    "infoboxes": [],
    "suggestions": [],
    "unresponsive_engines": [],
}


def _provider(handler: Any) -> SearxngProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, timeout=5.0)
    return SearxngProvider(base_url=_BASE, client=client)


def _json_provider(payload: Any, status_code: int = 200) -> SearxngProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        assert request.url.params["q"] == "wazuh sca configuration"
        assert request.url.params["format"] == "json"
        return httpx.Response(status_code, content=json.dumps(payload).encode())

    return _provider(handler)


# ── SearxngProvider ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_searxng_normalizes_documented_payload() -> None:
    provider = _json_provider(_PAYLOAD)
    hits = await provider.search("wazuh sca configuration", max_results=10)
    assert len(hits) == 3
    first = hits[0]
    assert first.url.startswith("https://documentation.wazuh.com/")
    assert first.title == "Security Configuration Assessment - Wazuh documentation"
    assert first.snippet.startswith("The SCA module")
    assert first.engine == "duckduckgo"
    assert first.published is None  # null publishedDate tolerated
    assert hits[1].published == "2024-03-01T00:00:00"  # camelCase alias mapped
    # Optional fields absent entirely → defaults, entry still kept.
    assert hits[2].snippet == ""
    assert hits[2].engine == ""


@pytest.mark.asyncio
async def test_searxng_caps_results_at_max_results() -> None:
    provider = _json_provider(_PAYLOAD)
    hits = await provider.search("wazuh sca configuration", max_results=2)
    assert len(hits) == 2
    assert hits[1].title == "SCA in depth"


@pytest.mark.asyncio
async def test_searxng_drops_entries_missing_url_or_title() -> None:
    # Schema validation per entry: broken hits are dropped, never guessed at;
    # the valid one survives.
    payload = {
        "results": [
            {"title": "no url here", "content": "x"},
            {"url": "https://example.com/no-title"},
            {"url": "https://example.com/ok", "title": "kept"},
        ]
    }
    provider = _json_provider(payload)
    hits = await provider.search("wazuh sca configuration", max_results=10)
    assert [h.title for h in hits] == ["kept"]


@pytest.mark.asyncio
async def test_searxng_empty_results_is_empty_list() -> None:
    provider = _json_provider({"results": []})
    assert await provider.search("wazuh sca configuration", max_results=5) == []


@pytest.mark.asyncio
async def test_searxng_non_200_raises_with_settings_hint() -> None:
    # SearXNG returns 403 when the json format is not enabled in settings.yml —
    # the error message points the operator at the fix.
    provider = _json_provider({"detail": "forbidden"}, status_code=403)
    with pytest.raises(SearchProviderError, match="HTTP 403.*settings.yml"):
        await provider.search("wazuh sca configuration", max_results=5)


@pytest.mark.asyncio
async def test_searxng_malformed_payload_raises() -> None:
    # Top-level shape violation (results not a list) → schema-validated error,
    # never passed through shape-unchecked (A6 §4).
    provider = _json_provider({"results": "not-a-list"})
    with pytest.raises(SearchProviderError, match="malformed"):
        await provider.search("wazuh sca configuration", max_results=5)


@pytest.mark.asyncio
async def test_searxng_non_json_body_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    provider = _provider(handler)
    with pytest.raises(SearchProviderError, match="malformed"):
        await provider.search("wazuh sca configuration", max_results=5)


@pytest.mark.asyncio
async def test_searxng_unreachable_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = _provider(handler)
    with pytest.raises(SearchProviderError, match="unreachable"):
        await provider.search("wazuh sca configuration", max_results=5)


# ── Resolver ────────────────────────────────────────────────────────────────


class _StubSecrets:
    """Minimal SecretsBackend — the resolver reserves it for hosted keys."""

    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str) -> None:  # pragma: no cover
        return None

    async def delete(self, key: str) -> None:  # pragma: no cover
        return None

    async def exists(self, key: str) -> bool:  # pragma: no cover
        return False


def _ctx() -> OrganizationContext:
    return OrganizationContext(
        organization_id=uuid.uuid4(),
        organization_slug="acme",
        user_id=uuid.uuid4(),
        user_email="analyst@example.com",
        role="analyst",
        session_id="sess-1",
    )


def test_web_search_disabled_is_the_code_default() -> None:
    # Pin the FIELD default (not a constructed Settings, which reads .env —
    # the 6-f.3 web-test will set WEB_SEARCH_ENABLED=1 there and must not
    # flip this test). A stock install ships with web research OFF.
    assert Settings.model_fields["web_search_enabled"].default is False


def test_searxng_url_default_is_the_wolf_search_port() -> None:
    # 1307 = the operator-chosen wolf-search port (6-f.2 live install);
    # SearXNG's own default 8888 is NOT what wolf-search binds.
    assert Settings.model_fields["searxng_url"].default == "http://127.0.0.1:1307"


@pytest.mark.asyncio
async def test_resolver_fails_closed_when_disabled() -> None:
    # Resolving while disabled is a wiring bug — callers gate registration.
    settings = Settings(web_search_enabled=False)
    with pytest.raises(SearchProviderUnconfiguredError, match="WEB_SEARCH_ENABLED"):
        await get_search_provider_for_organization(_ctx(), settings, _StubSecrets())


@pytest.mark.asyncio
async def test_resolver_builds_searxng_from_settings() -> None:
    settings = Settings(web_search_enabled=True, searxng_url="http://127.0.0.1:9999/")
    provider = await get_search_provider_for_organization(_ctx(), settings, _StubSecrets())
    assert isinstance(provider, SearxngProvider)
    assert isinstance(provider, SearchProvider)  # satisfies the protocol
    assert provider.name == "searxng"
    # Trailing slash normalized so the adapter never builds `//search`.
    assert provider._base_url == "http://127.0.0.1:9999"


@pytest.mark.asyncio
async def test_resolver_normalizes_provider_case() -> None:
    settings = Settings(web_search_enabled=True, web_search_provider="  SearXNG ")
    provider = await get_search_provider_for_organization(_ctx(), settings, _StubSecrets())
    assert isinstance(provider, SearxngProvider)


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", ["brave", "tavily"])
async def test_resolver_deferred_hosted_backends_say_so(deferred: str) -> None:
    settings = Settings(web_search_enabled=True, web_search_provider=deferred)
    with pytest.raises(SearchProviderUnconfiguredError, match="not wired yet"):
        await get_search_provider_for_organization(_ctx(), settings, _StubSecrets())


@pytest.mark.asyncio
async def test_resolver_unknown_provider_is_distinct_error() -> None:
    settings = Settings(web_search_enabled=True, web_search_provider="askjeeves")
    with pytest.raises(SearchProviderUnconfiguredError, match="Unknown search provider"):
        await get_search_provider_for_organization(_ctx(), settings, _StubSecrets())
