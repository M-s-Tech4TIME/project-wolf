"""URL validation + SSRF guard (ADR 0032 A6 §1/§10, slice 6-f.3).

Pins the security contract of `research/weburl.py`: scheme allowlist,
credential rejection, punycode normalization, and — the load-bearing part —
post-resolution address vetting with pinning. DNS is stubbed via the
injectable resolver (hermetic CI).
"""

import pytest
from wolf_server.research.weburl import (
    WebUrlError,
    resolve_pinned_ip,
    validate_url,
)

# ── Syntactic validation ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "file:///etc/passwd",
        "gopher://example.com/",
        "data:text/html,hi",
        "javascript:alert(1)",
        "example.com/no-scheme",
        "",
    ],
)
def test_non_http_schemes_rejected(url: str) -> None:
    with pytest.raises(WebUrlError):
        validate_url(url)


def test_credentials_in_url_rejected() -> None:
    with pytest.raises(WebUrlError, match="credentials"):
        validate_url("https://user:secret@example.com/")


def test_missing_host_rejected() -> None:
    with pytest.raises(WebUrlError, match="no host"):
        validate_url("https:///path-only")


def test_invalid_port_rejected() -> None:
    with pytest.raises(WebUrlError, match="port"):
        validate_url("https://example.com:99999/")


def test_valid_url_decomposed_with_defaults() -> None:
    v = validate_url("https://Example.COM/a/b?x=1")
    assert v.scheme == "https"
    assert v.host == "example.com"  # lower-cased
    assert v.port == 443  # scheme default
    assert v.path_and_query == "/a/b?x=1"


def test_http_default_port_and_bare_path() -> None:
    v = validate_url("http://example.com")
    assert v.port == 80
    assert v.path_and_query == "/"


def test_idn_host_punycode_normalized() -> None:
    # Homograph defense (§10): the Cyrillic host normalizes to its xn-- form,
    # which is what the allowlist matching sees.
    v = validate_url("https://пример.com/x")
    assert v.host.startswith("xn--")


# ── Post-resolution vetting + pinning ────────────────────────────────────────

_FORBIDDEN = {
    "metadata": "169.254.169.254",  # cloud metadata (link-local v4)
    "loopback": "127.0.0.1",
    "loopback-alias": "127.8.8.8",
    "mapped-loopback": "::ffff:127.0.0.1",  # IPv4-mapped IPv6 bypass
    "rfc1918-10": "10.0.0.5",
    "rfc1918-172": "172.16.0.9",
    "rfc1918-192": "192.168.1.1",
    "ula": "fd00:ec2::254",  # AWS IMDS IPv6 (unique-local)
    "link-local-v6": "fe80::1",
    "unspecified": "0.0.0.0",
    "v6-loopback": "::1",
    "multicast": "224.0.0.1",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("address", sorted(_FORBIDDEN.values()))
async def test_forbidden_addresses_rejected(address: str) -> None:
    async def resolver(host: str, port: int) -> list[str]:
        return [address]

    with pytest.raises(WebUrlError, match="SSRF guard"):
        await resolve_pinned_ip(validate_url("http://example.com/"), resolver=resolver)


@pytest.mark.asyncio
async def test_any_forbidden_address_taints_the_whole_host() -> None:
    # Mixed record: one public + one private address → the HOST is rejected
    # (an attacker-controlled record must not be fetchable by luck).
    async def resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34", "192.168.1.1"]

    with pytest.raises(WebUrlError, match="SSRF guard"):
        await resolve_pinned_ip(validate_url("http://example.com/"), resolver=resolver)


@pytest.mark.asyncio
async def test_public_host_returns_pinned_ip() -> None:
    async def resolver(host: str, port: int) -> list[str]:
        assert host == "example.com"
        assert port == 443
        return ["2606:2800:220:1:248:1893:25c8:1946", "93.184.216.34"]

    pinned = await resolve_pinned_ip(validate_url("https://example.com/"), resolver=resolver)
    assert pinned == "93.184.216.34"  # IPv4 preferred for connectivity


@pytest.mark.asyncio
async def test_unresolvable_and_empty_hosts_rejected() -> None:
    async def empty(host: str, port: int) -> list[str]:
        return []

    with pytest.raises(WebUrlError, match="no addresses"):
        await resolve_pinned_ip(validate_url("http://example.com/"), resolver=empty)


@pytest.mark.asyncio
async def test_ip_literal_urls_are_vetted_too() -> None:
    # `http://127.0.0.1/` (and decimal/hex encodings, which getaddrinfo
    # resolves to the same numeric address) go through the same check.
    async def resolver(host: str, port: int) -> list[str]:
        return [host]  # an IP literal "resolves" to itself

    with pytest.raises(WebUrlError, match="SSRF guard"):
        await resolve_pinned_ip(validate_url("http://127.0.0.1:1307/"), resolver=resolver)
