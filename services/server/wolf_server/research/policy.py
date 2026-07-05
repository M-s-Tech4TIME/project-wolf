"""Docs-first retrieval policy + domain classification (ADR 0032 A4, A6 §10).

The priority ladder — documentation.wazuh.com → wazuh.com/blog →
github.com/wazuh → broader community — is implemented as source-tier
classification + a stable re-rank: official hits float above community hits
without discarding anything (the fall-through to community IS the miss
handling). Each result carries its tier so the model can prefer official
sources and the evidence panel can visually distinguish them (A5).

Domain matching is suffix-safe on the punycode-normalized host —
``documentation.wazuh.com.evil.com`` does NOT match ``wazuh.com`` because
matching requires equality or a ``.``-anchored suffix, defeating the
suffix-spoof and homograph tricks (§10).

The blocklist ships EMPTY as a wired mechanism: Wolf hardcodes no third-party
domain as "bad" — the operator curates it (a Phase 6.10 config consumer).
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

from wolf_server.research.interface import SearchResult
from wolf_server.research.weburl import WebUrlError, validate_url

# Source tiers, best-first. `web_search` results are re-ranked in this
# order; the tier string rides each result + citation.
TIER_OFFICIAL_DOCS = "official_docs"  # documentation.wazuh.com
TIER_OFFICIAL = "official"  # wazuh.com (blog, product pages)
TIER_OFFICIAL_GITHUB = "official_github"  # github.com/wazuh/*
TIER_COMMUNITY = "community"  # everything else

_TIER_RANK = {
    TIER_OFFICIAL_DOCS: 0,
    TIER_OFFICIAL: 1,
    TIER_OFFICIAL_GITHUB: 2,
    TIER_COMMUNITY: 3,
}

# Operator-curated blocklist of registrable domains Wolf refuses to search
# or fetch (SEO-spam / known-bad sources). Deliberately empty by default —
# shipping hardcoded third-party "bad domain" judgments is not Wolf's call;
# curation becomes an operator knob in the Phase 6.10 config plane.
BLOCKED_DOMAINS: frozenset[str] = frozenset()


def _host_matches(host: str, domain: str) -> bool:
    """Suffix-safe host-vs-domain match: equal, or a `.`-anchored subdomain."""
    return host == domain or host.endswith("." + domain)


def _host_of(url: str) -> str | None:
    """The punycode-normalized host of a URL, or None if the URL is invalid."""
    try:
        return validate_url(url).host
    except WebUrlError:
        return None


def is_blocked(url: str) -> bool:
    """True when the URL's host falls under a blocklisted domain."""
    host = _host_of(url)
    if host is None:
        return False  # invalid URLs are rejected by the fetch guard anyway
    return any(_host_matches(host, blocked) for blocked in BLOCKED_DOMAINS)


def classify_source(url: str) -> str:
    """Classify a URL into its docs-first source tier."""
    host = _host_of(url)
    if host is None:
        return TIER_COMMUNITY
    if _host_matches(host, "documentation.wazuh.com"):
        return TIER_OFFICIAL_DOCS
    if _host_matches(host, "wazuh.com"):
        return TIER_OFFICIAL
    if _host_matches(host, "github.com"):
        # Official only for the wazuh org's repos — path-aware.
        path = urlsplit(url).path.lower()
        if path.startswith("/wazuh/") or path == "/wazuh":
            return TIER_OFFICIAL_GITHUB
    return TIER_COMMUNITY


@dataclass(frozen=True)
class RankedResult:
    """A search hit annotated with its docs-first source tier."""

    result: SearchResult
    source: str


def rank_docs_first(results: list[SearchResult]) -> list[RankedResult]:
    """Annotate with tiers, drop blocklisted hits, re-rank official-first.

    The sort is stable: within a tier, the backend's own relevance order is
    preserved. Nothing outside the blocklist is discarded — community hits
    simply rank below official ones (the docs-first "fall through").
    """
    kept = [
        RankedResult(result=result, source=classify_source(result.url))
        for result in results
        if not is_blocked(result.url)
    ]
    return sorted(kept, key=lambda ranked: _TIER_RANK[ranked.source])


# ── Registrable-domain approximation (crawl same-domain scope) ───────────────

# Common second-level public suffixes, so `docs.wazuh.co.uk` → `wazuh.co.uk`
# rather than `co.uk`. A deliberate stdlib-only approximation of the Public
# Suffix List (lean-wheels, ADR 0007): unlisted multi-part suffixes degrade
# to the last-two-labels rule, which for the crawler errs on the SAFE side
# only via the seed's own domain — and every crawled URL still passes the
# full SSRF/URL guard regardless of this scope check.
_SECOND_LEVEL_SUFFIXES = frozenset(
    {
        "co.uk", "org.uk", "ac.uk", "gov.uk", "net.uk",
        "com.au", "net.au", "org.au", "edu.au", "gov.au",
        "co.nz", "org.nz", "net.nz",
        "co.jp", "or.jp", "ne.jp", "ac.jp", "go.jp",
        "com.br", "org.br", "net.br",
        "com.mx", "org.mx",
        "co.in", "org.in", "net.in",
        "co.za", "org.za",
        "com.cn", "org.cn", "net.cn",
        "com.tr", "org.tr",
        "com.ar", "org.ar",
        "co.kr", "or.kr",
        "com.sg", "org.sg",
        "com.hk", "org.hk",
    }
)  # fmt: skip


def registrable_domain(host: str) -> str:
    """Approximate eTLD+1 of an already-normalized host.

    ``documentation.wazuh.com`` → ``wazuh.com``; ``docs.x.co.uk`` → ``x.co.uk``.
    Bare or single-label hosts return themselves.
    """
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if ".".join(labels[-2:]) in _SECOND_LEVEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def same_registrable_domain(url: str, seed_host: str) -> bool:
    """True when `url`'s host shares the seed's registrable domain.

    The crawler's same-domain traversal check (ADR 0032 A1): a crawl seeded
    at ``documentation.wazuh.com`` may follow links to ``wazuh.com`` hosts
    but never off the wazuh.com registrable domain.
    """
    host = _host_of(url)
    if host is None:
        return False
    return registrable_domain(host) == registrable_domain(seed_host)
