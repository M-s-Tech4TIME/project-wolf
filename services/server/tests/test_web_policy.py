"""Docs-first policy + domain classification (ADR 0032 A4/A6 §10, slice 6-f.3)."""

import pytest
from wolf_server.research import policy
from wolf_server.research.interface import SearchResult


def _hit(url: str, title: str = "t") -> SearchResult:
    return SearchResult(url=url, title=title)


# ── Tier classification ──────────────────────────────────────────────────────


def test_official_docs_tier() -> None:
    assert (
        policy.classify_source("https://documentation.wazuh.com/current/x.html")
        == policy.TIER_OFFICIAL_DOCS
    )


def test_wazuh_com_tier_includes_subdomains() -> None:
    assert policy.classify_source("https://wazuh.com/blog/post") == policy.TIER_OFFICIAL
    assert policy.classify_source("https://www.wazuh.com/") == policy.TIER_OFFICIAL


def test_github_official_is_path_aware() -> None:
    assert (
        policy.classify_source("https://github.com/wazuh/wazuh-ruleset")
        == policy.TIER_OFFICIAL_GITHUB
    )
    # Other org's repos are community, not official.
    assert policy.classify_source("https://github.com/acme/wazuh-fork") == policy.TIER_COMMUNITY


def test_suffix_spoof_is_community() -> None:
    # §10: `documentation.wazuh.com.evil.com` must NOT classify as official.
    assert (
        policy.classify_source("https://documentation.wazuh.com.evil.com/x")
        == policy.TIER_COMMUNITY
    )


def test_unparseable_url_is_community() -> None:
    assert policy.classify_source("not a url") == policy.TIER_COMMUNITY


# ── Docs-first ranking ───────────────────────────────────────────────────────


def test_rank_docs_first_is_stable_official_first() -> None:
    ranked = policy.rank_docs_first(
        [
            _hit("https://stackoverflow.com/q/1", "community-1"),
            _hit("https://wazuh.com/blog/a", "blog"),
            _hit("https://stackoverflow.com/q/2", "community-2"),
            _hit("https://documentation.wazuh.com/a", "docs"),
        ]
    )
    assert [r.result.title for r in ranked] == ["docs", "blog", "community-1", "community-2"]
    assert ranked[0].source == policy.TIER_OFFICIAL_DOCS
    # Community hits keep the backend's relative order (stable sort).
    assert [r.result.title for r in ranked if r.source == policy.TIER_COMMUNITY] == [
        "community-1",
        "community-2",
    ]


def test_blocklist_mechanism_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    # The blocklist ships EMPTY (operator-curated later — Phase 6.10); the
    # MECHANISM is pinned here with a patched entry.
    monkeypatch.setattr(policy, "BLOCKED_DOMAINS", frozenset({"spam.example"}))
    assert policy.is_blocked("https://seo.spam.example/page") is True
    assert policy.is_blocked("https://example.com/") is False
    ranked = policy.rank_docs_first(
        [_hit("https://spam.example/x", "spam"), _hit("https://example.com/", "ok")]
    )
    assert [r.result.title for r in ranked] == ["ok"]


def test_blocklist_default_is_empty() -> None:
    assert len(policy.BLOCKED_DOMAINS) == 0


# ── Registrable domain (crawl scope) ─────────────────────────────────────────


def test_registrable_domain_basic_and_second_level() -> None:
    assert policy.registrable_domain("documentation.wazuh.com") == "wazuh.com"
    assert policy.registrable_domain("wazuh.com") == "wazuh.com"
    assert policy.registrable_domain("docs.acme.co.uk") == "acme.co.uk"
    assert policy.registrable_domain("localhost") == "localhost"


def test_same_registrable_domain_bounds_the_crawl() -> None:
    seed = "documentation.wazuh.com"
    assert policy.same_registrable_domain("https://wazuh.com/blog", seed) is True
    assert policy.same_registrable_domain("https://documentation.wazuh.com/a", seed) is True
    assert policy.same_registrable_domain("https://evil.com/wazuh.com", seed) is False
    assert policy.same_registrable_domain("https://wazuh.com.evil.com/", seed) is False
    assert policy.same_registrable_domain("garbage", seed) is False
