"""Bounded, polite, query-driven crawler behind `web_crawl` (ADR 0032 A1, A6 §11).

"Read a site fully" — bounded. Never an unbounded spider:

- **Same registrable domain** as the seed, always (policy.same_registrable_domain).
- **Hard caps**: depth from the seed, total pages, plus the fetcher's
  per-page byte cap and one overall wall-clock deadline for the whole crawl.
- **robots.txt respected** per host (stdlib parser over a guarded fetch);
  an unreadable robots file fails OPEN for that host (standard crawler
  convention — absence of robots.txt means no restrictions) while every
  page still passes the full SSRF guard.
- **Sitemap-first** discovery: `sitemap.xml` entries (from robots.txt
  `Sitemap:` lines or the conventional root path) seed the frontier before
  any link-spidering. Sitemap XML is read with a regex `<loc>` scan, not an
  XML parser — immune to entity-expansion bombs by construction (§4).
- **Per-host politeness delay** between requests; honest User-Agent (the
  fetcher's); no block-evasion of any kind.
- **Query-driven**: the frontier is a best-first queue scored by overlap
  between the analyst's query and each candidate's URL/anchor text — Wolf
  reads the most relevant pages up to the caps, then stops.
"""

import asyncio
import re
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import structlog

from wolf_server.research import policy
from wolf_server.research.fetcher import USER_AGENT, FetchedPage, WebFetcher, WebFetchError
from wolf_server.research.weburl import WebUrlError, validate_url

logger = structlog.get_logger(__name__)

_SITEMAP_LOC = re.compile(r"<loc>\s*(https?://[^<\s]+)\s*</loc>", re.IGNORECASE)
_ROBOTS_SITEMAP = re.compile(r"^\s*sitemap:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_WORD = re.compile(r"[a-z0-9]+")
# One overall deadline for the whole crawl — the backstop above the per-page
# fetch timeout, so even max_pages slow-ish pages can't stall the loop.
_CRAWL_DEADLINE_SECONDS = 120.0
# Cap on sitemap-discovered candidates fed into the frontier (a huge site's
# sitemap must not swamp scoring).
_MAX_SITEMAP_CANDIDATES = 500


@dataclass
class CrawledPage:
    """One page the crawl fetched, plus its distance from the seed."""

    page: FetchedPage
    depth: int


@dataclass
class CrawlOutcome:
    """The crawl's result set + honest bookkeeping for the model."""

    pages: list[CrawledPage] = field(default_factory=list)
    skipped_robots: int = 0
    skipped_offdomain: int = 0
    fetch_errors: int = 0
    hit_page_cap: bool = False
    hit_deadline: bool = False


def _score(query_terms: set[str], url: str, anchor_text: str = "") -> int:
    """Relevance of a candidate: query-term overlap with its URL + anchor."""
    haystack = set(_WORD.findall(url.lower())) | set(_WORD.findall(anchor_text.lower()))
    return len(query_terms & haystack)


class BoundedCrawler:
    """One crawl = one instance; holds per-host robots + politeness state."""

    def __init__(
        self,
        *,
        fetcher: WebFetcher,
        max_depth: int,
        max_pages: int,
        per_host_delay_seconds: float,
    ) -> None:
        self._fetcher = fetcher
        self._max_depth = max_depth
        self._max_pages = max_pages
        self._per_host_delay = per_host_delay_seconds
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}

    async def crawl(self, seed_url: str, query: str) -> CrawlOutcome:
        """Run the bounded crawl. Raises WebUrlError only for a bad SEED."""
        seed = validate_url(seed_url)  # a bad seed is the caller's error
        query_terms = set(_WORD.findall(query.lower()))
        outcome = CrawlOutcome()
        try:
            async with asyncio.timeout(_CRAWL_DEADLINE_SECONDS):
                await self._crawl_inner(seed_url, seed.host, query_terms, outcome)
        except TimeoutError:
            outcome.hit_deadline = True
            logger.info("web_crawl_deadline", seed=seed_url, pages=len(outcome.pages))
        return outcome

    async def _crawl_inner(
        self,
        seed_url: str,
        seed_host: str,
        query_terms: set[str],
        outcome: CrawlOutcome,
    ) -> None:
        # Frontier entries: (score, order, url, depth). Best-first by score,
        # FIFO within equal scores (order breaks ties deterministically).
        frontier: list[tuple[int, int, str, int]] = []
        order = 0
        visited: set[str] = set()

        def push(url: str, depth: int, anchor: str = "", score: int | None = None) -> None:
            nonlocal order
            if url in visited:
                return
            visited.add(url)
            computed = _score(query_terms, url, anchor) if score is None else score
            frontier.append((computed, order, url, depth))
            order += 1

        # The seed always reads first — the user pointed at it; discovered
        # candidates compete on query relevance only after it.
        push(seed_url, 0, score=1_000_000)
        # Sitemap-first: discovered URLs join the frontier at depth 1 —
        # they are one discovery step from the seed.
        for sitemap_url in await self._sitemap_candidates(seed_url, seed_host):
            if self._max_depth >= 1:
                push(sitemap_url, 1)

        while frontier and len(outcome.pages) < self._max_pages:
            frontier.sort(key=lambda entry: (-entry[0], entry[1]))
            _score_, _order_, url, depth = frontier.pop(0)

            if not policy.same_registrable_domain(url, seed_host) or policy.is_blocked(url):
                outcome.skipped_offdomain += 1
                continue
            if not await self._robots_allows(url):
                outcome.skipped_robots += 1
                continue

            await self._politeness_delay(url)
            try:
                page = await self._fetcher.fetch(url)
            except (WebUrlError, WebFetchError) as exc:
                outcome.fetch_errors += 1
                logger.info("web_crawl_page_failed", url=url, error=str(exc))
                continue

            outcome.pages.append(CrawledPage(page=page, depth=depth))
            if depth < self._max_depth:
                # Pushed unfiltered — the pop-time domain/robots checks are
                # the single enforcement point (and count what they skip).
                for link in page.links:
                    push(link, depth + 1)

        if frontier and len(outcome.pages) >= self._max_pages:
            outcome.hit_page_cap = True

    # ── Politeness ──────────────────────────────────────────────────────────

    async def _politeness_delay(self, url: str) -> None:
        host = urlsplit(url).hostname or ""
        now = time.monotonic()
        last = self._last_hit.get(host)
        if last is not None:
            wait = self._per_host_delay - (now - last)
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_hit[host] = time.monotonic()

    async def _robots_allows(self, url: str) -> bool:
        host = urlsplit(url).hostname or ""
        if host not in self._robots:
            self._robots[host] = await self._load_robots(url, host)
        parser = self._robots[host]
        if parser is None:
            return True  # no readable robots.txt → unrestricted (convention)
        return parser.can_fetch(USER_AGENT, url)

    async def _load_robots(
        self, sample_url: str, host: str
    ) -> urllib.robotparser.RobotFileParser | None:
        scheme = urlsplit(sample_url).scheme
        try:
            fetched = await self._fetcher.fetch(f"{scheme}://{host}/robots.txt")
        except (WebUrlError, WebFetchError):
            return None
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(fetched.text.splitlines())
        return parser

    # ── Sitemap discovery ───────────────────────────────────────────────────

    async def _sitemap_candidates(self, seed_url: str, host: str) -> list[str]:
        """URLs from the host's sitemap(s), same-domain-filtered and capped."""
        scheme = urlsplit(seed_url).scheme
        sitemap_urls = [f"{scheme}://{host}/sitemap.xml"]
        # robots.txt may point at the real sitemap location.
        try:
            robots = await self._fetcher.fetch(f"{scheme}://{host}/robots.txt")
            sitemap_urls = _ROBOTS_SITEMAP.findall(robots.text) or sitemap_urls
        except (WebUrlError, WebFetchError):
            pass

        candidates: list[str] = []
        for sitemap_url in sitemap_urls[:3]:  # a handful of indexes at most
            try:
                sitemap = await self._fetcher.fetch(sitemap_url)
            except (WebUrlError, WebFetchError):
                continue
            for loc in _SITEMAP_LOC.findall(sitemap.text):
                if policy.same_registrable_domain(loc, host):
                    candidates.append(loc)
                if len(candidates) >= _MAX_SITEMAP_CANDIDATES:
                    return candidates
        return candidates
