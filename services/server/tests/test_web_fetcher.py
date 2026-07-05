"""The SSRF-guarded fetcher (ADR 0032 A2/A6, slice 6-f.3) — hermetic.

Both boundaries are stubbed: httpx via MockTransport, DNS via the injectable
resolver. Pins the guard mechanics end-to-end: the socket dials the PINNED
IP (with the hostname in Host + SNI), redirects are re-validated per hop,
the DECOMPRESSED byte cap truncates, non-text content is refused, and
failures surface as clean errors (§14).
"""

from collections.abc import Callable

import httpx
import pytest
from wolf_server.research.fetcher import WebFetcher, WebFetchError
from wolf_server.research.weburl import WebUrlError

_PUBLIC_IPS = {
    "example.com": "93.184.216.34",
    "other.example.org": "8.8.4.4",
    "internal.corp": "10.0.0.5",
}


async def _resolver(host: str, port: int) -> list[str]:
    return [_PUBLIC_IPS[host]]


def _fetcher(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_bytes: int = 100_000,
    timeout: float = 5.0,
) -> WebFetcher:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)
    return WebFetcher(
        max_bytes=max_bytes, timeout_seconds=timeout, client=client, resolver=_resolver
    )


_HTML = b"""<html><head><title>Guide</title><script>evil()</script></head>
<body><nav>menu</nav><h1>Wazuh</h1><p>Install the agent.
<a href="/next">next page</a></p><footer>foot</footer></body></html>"""


@pytest.mark.asyncio
async def test_fetch_dials_pinned_ip_with_host_and_sni() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url_host"] = request.url.host
        seen["host_header"] = request.headers["host"]
        seen["sni"] = request.extensions.get("sni_hostname")
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, content=_HTML, headers={"content-type": "text/html"})

    page = await _fetcher(handler).fetch("https://example.com/guide")
    # The socket dials the vetted IP; the hostname rides Host + SNI so TLS
    # verification still runs against the real name (§1 pinning).
    assert seen["url_host"] == "93.184.216.34"
    assert seen["host_header"] == "example.com"
    assert seen["sni"] == "example.com"
    assert "wolf-research" in str(seen["ua"])  # honest UA (§11)
    assert page.title == "Guide"
    assert "Install the agent." in page.text
    assert "evil()" not in page.text  # script stripped
    assert "menu" not in page.text  # chrome stripped
    assert page.links == ["https://example.com/next"]
    assert page.final_url == "https://example.com/guide"


@pytest.mark.asyncio
async def test_redirect_hops_are_revalidated_and_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "example.com":
            return httpx.Response(301, headers={"location": "https://other.example.org/moved"})
        assert request.url.host == "8.8.4.4"  # second hop re-resolved + pinned
        return httpx.Response(200, content=b"<p>arrived</p>", headers={"content-type": "text/html"})

    page = await _fetcher(handler).fetch("https://example.com/old")
    assert page.url == "https://example.com/old"
    assert page.final_url == "https://other.example.org/moved"
    assert "arrived" in page.text


@pytest.mark.asyncio
async def test_redirect_to_private_target_rejected() -> None:
    # A public page redirecting into RFC-1918 space — the classic SSRF
    # bounce — is caught by the per-hop re-validation (§1).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://internal.corp/admin"})

    with pytest.raises(WebUrlError, match="SSRF guard"):
        await _fetcher(handler).fetch("https://example.com/")


@pytest.mark.asyncio
async def test_redirect_loop_capped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/again"})

    with pytest.raises(WebFetchError, match="redirected more than"):
        await _fetcher(handler).fetch("https://example.com/loop")


@pytest.mark.asyncio
async def test_decompressed_byte_cap_truncates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"A" * 5000, headers={"content-type": "text/plain"})

    page = await _fetcher(handler, max_bytes=1000).fetch("https://example.com/big")
    assert page.truncated is True
    assert len(page.text) <= 1000


@pytest.mark.asyncio
async def test_binary_content_type_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"\x7fELF", headers={"content-type": "application/octet-stream"}
        )

    with pytest.raises(WebFetchError, match="content type"):
        await _fetcher(handler).fetch("https://example.com/blob")


@pytest.mark.asyncio
async def test_non_200_is_a_clean_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"nope")

    with pytest.raises(WebFetchError, match="HTTP 404"):
        await _fetcher(handler).fetch("https://example.com/missing")


@pytest.mark.asyncio
async def test_json_body_passed_through_without_html_extraction() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b'{"a": 1}', headers={"content-type": "application/json"}
        )

    page = await _fetcher(handler).fetch("https://example.com/api")
    assert page.text == '{"a": 1}'
    assert page.links == []


@pytest.mark.asyncio
async def test_transport_error_is_a_clean_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(WebFetchError, match="failed"):
        await _fetcher(handler).fetch("https://example.com/")


@pytest.mark.asyncio
async def test_bad_scheme_rejected_before_any_io() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not be reached")

    with pytest.raises(WebUrlError, match="scheme"):
        await _fetcher(handler).fetch("file:///etc/passwd")


@pytest.mark.asyncio
async def test_control_chars_sanitized_from_extracted_text() -> None:
    # Log-forging defense (§12): control chars never survive extraction.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"line\x1b[31mred\x00null",
            headers={"content-type": "text/plain"},
        )

    page = await _fetcher(handler).fetch("https://example.com/log")
    assert "\x1b" not in page.text
    assert "\x00" not in page.text
