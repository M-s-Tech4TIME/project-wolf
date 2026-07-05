"""The bounded crawler (ADR 0032 A1/A6 §11, slice 6-f.3) — hermetic.

A fake site is served through MockTransport (routed by the Host header,
since the fetcher dials pinned IPs). Pins the bounds that make `web_crawl`
"never an unbounded spider": robots.txt respected, same-registrable-domain
enforced, sitemap-first discovery, page/depth caps, query-driven ordering.
"""

import httpx
import pytest
from wolf_server.research.crawl import BoundedCrawler
from wolf_server.research.fetcher import WebFetcher

_IPS = {
    "docs.example.com": "93.184.216.34",
    "blog.example.com": "93.184.216.35",
    "evil.com": "9.9.9.9",
}


async def _resolver(host: str, port: int) -> list[str]:
    return [_IPS[host]]


_SITE: dict[tuple[str, str], tuple[str, str]] = {
    # (host, path) -> (content_type, body)
    ("docs.example.com", "/robots.txt"): (
        "text/plain",
        "User-agent: *\nDisallow: /private/\nSitemap: https://docs.example.com/sitemap.xml\n",
    ),
    ("docs.example.com", "/sitemap.xml"): (
        "application/xml",
        """<?xml version="1.0"?><urlset>
        <url><loc>https://docs.example.com/guide/wazuh-agent</loc></url>
        <url><loc>https://evil.com/off-domain</loc></url>
        </urlset>""",
    ),
    ("docs.example.com", "/"): (
        "text/html",
        "<html><head><title>Home</title></head><body>"
        '<a href="/guide/wazuh-agent">agent guide</a>'
        '<a href="/private/secret">secret</a>'
        '<a href="https://evil.com/page">elsewhere</a>'
        '<a href="https://blog.example.com/wazuh-post">blog</a>'
        "</body></html>",
    ),
    ("docs.example.com", "/guide/wazuh-agent"): (
        "text/html",
        "<html><head><title>Agent guide</title></head><body>Install steps."
        '<a href="/guide/deeper">deeper</a></body></html>',
    ),
    ("docs.example.com", "/guide/deeper"): (
        "text/html",
        "<html><head><title>Deeper</title></head><body>Depth-2 page.</body></html>",
    ),
    ("docs.example.com", "/private/secret"): (
        "text/html",
        "<html><body>robots-disallowed</body></html>",
    ),
    ("blog.example.com", "/robots.txt"): ("text/plain", "User-agent: *\nAllow: /\n"),
    ("blog.example.com", "/wazuh-post"): (
        "text/html",
        "<html><head><title>Blog post</title></head><body>Same registrable domain.</body></html>",
    ),
}


def _handler(request: httpx.Request) -> httpx.Response:
    host = request.headers["host"]
    key = (host, request.url.path)
    if key not in _SITE:
        return httpx.Response(404, content=b"not found")
    content_type, body = _SITE[key]
    return httpx.Response(200, content=body.encode(), headers={"content-type": content_type})


def _crawler(*, max_depth: int = 2, max_pages: int = 10) -> BoundedCrawler:
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler), timeout=5.0)
    fetcher = WebFetcher(max_bytes=100_000, timeout_seconds=5.0, client=client, resolver=_resolver)
    return BoundedCrawler(
        fetcher=fetcher,
        max_depth=max_depth,
        max_pages=max_pages,
        per_host_delay_seconds=0.0,  # tests need no politeness pauses
    )


@pytest.mark.asyncio
async def test_crawl_respects_robots_domain_and_discovers_via_sitemap() -> None:
    outcome = await _crawler().crawl("https://docs.example.com/", "wazuh agent guide")
    urls = {p.page.final_url for p in outcome.pages}
    # Seed + sitemap/link discoveries on the registrable domain:
    assert "https://docs.example.com/" in urls
    assert "https://docs.example.com/guide/wazuh-agent" in urls
    # Subdomain of the same registrable domain is IN scope:
    assert "https://blog.example.com/wazuh-post" in urls
    # robots.txt Disallow honored:
    assert all("/private/" not in u for u in urls)
    assert outcome.skipped_robots >= 1
    # Off-domain never fetched (sitemap and link both pointed at evil.com):
    assert all("evil.com" not in u for u in urls)
    assert outcome.skipped_offdomain >= 1


@pytest.mark.asyncio
async def test_page_cap_bounds_the_crawl() -> None:
    outcome = await _crawler(max_pages=2).crawl("https://docs.example.com/", "wazuh")
    assert len(outcome.pages) == 2
    assert outcome.hit_page_cap is True


@pytest.mark.asyncio
async def test_depth_zero_fetches_only_the_seed() -> None:
    outcome = await _crawler(max_depth=0).crawl("https://docs.example.com/", "wazuh")
    assert [p.page.final_url for p in outcome.pages] == ["https://docs.example.com/"]
    assert all(p.depth == 0 for p in outcome.pages)


@pytest.mark.asyncio
async def test_depth_cap_stops_link_expansion() -> None:
    # depth 1: seed (0) + its links/sitemap (1), but /guide/deeper (2) is
    # never followed.
    outcome = await _crawler(max_depth=1).crawl("https://docs.example.com/", "wazuh guide")
    urls = {p.page.final_url for p in outcome.pages}
    assert "https://docs.example.com/guide/wazuh-agent" in urls
    assert "https://docs.example.com/guide/deeper" not in urls


@pytest.mark.asyncio
async def test_query_terms_steer_fetch_order() -> None:
    # With room for the seed + one more page, the query-matching URL wins
    # the frontier over the non-matching one.
    outcome = await _crawler(max_pages=2).crawl("https://docs.example.com/", "wazuh agent")
    urls = [p.page.final_url for p in outcome.pages]
    assert urls[0] == "https://docs.example.com/"
    assert "wazuh" in urls[1]


@pytest.mark.asyncio
async def test_fetch_errors_counted_not_fatal() -> None:
    site_404 = {("docs.example.com", "/robots.txt"), ("docs.example.com", "/sitemap.xml")}

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.headers["host"], request.url.path)
        if key in site_404 or request.url.path == "/gone":
            return httpx.Response(404, content=b"x")
        return httpx.Response(
            200,
            content=b'<html><body><a href="/gone">gone</a></body></html>',
            headers={"content-type": "text/html"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    fetcher = WebFetcher(max_bytes=100_000, timeout_seconds=5.0, client=client, resolver=_resolver)
    crawler = BoundedCrawler(fetcher=fetcher, max_depth=1, max_pages=5, per_host_delay_seconds=0.0)
    outcome = await crawler.crawl("https://docs.example.com/", "anything")
    assert len(outcome.pages) == 1  # the seed
    assert outcome.fetch_errors == 1  # /gone 404'd, crawl carried on
