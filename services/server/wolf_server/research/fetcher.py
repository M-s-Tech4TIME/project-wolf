"""Provider-independent, SSRF-guarded page fetcher (ADR 0032 A2, A6 §1/§4/§10/§14).

One fetch path shared by `web_fetch`, `web_crawl`, and the crawler's
robots/sitemap reads, regardless of which search backend is active.

Security posture, per hop:
- URL is validated (scheme/creds/host — `weburl.validate_url`), the host is
  resolved, EVERY resolved address is vetted, and the connection is made to
  the **pinned IP** (DNS-rebinding defense): the request goes to
  ``https://<ip>/…`` with the real hostname carried in the ``Host`` header
  and as the TLS SNI/verification name (`sni_hostname`), so certificate
  verification still runs against the hostname (never ``verify=False``).
- Redirects are never auto-followed: each ``Location`` is re-validated and
  re-resolved from scratch, with a hard hop cap.
- The DECOMPRESSED body is streamed with a hard cap — a gzip bomb is cut off
  mid-stream, not after inflation (§4). The whole fetch runs under one
  deadline (slow-loris guard, §14).
- Only text-ish content types are accepted; fetched bytes are never executed
  or persisted as a file (§4).
- No auth/cookie state exists to forward; the User-Agent is honest (§11).

The httpx client and the DNS resolver are injectable so tests stub both
boundaries (hermetic CI — same pattern as `SearxngProvider`).
"""

import asyncio
from dataclasses import dataclass, field

import httpx
import structlog
from wolf_common.errors import WolfError

from wolf_server.research.extract import extract_html, sanitize_text
from wolf_server.research.weburl import (
    Resolver,
    ValidatedUrl,
    resolve_pinned_ip,
    validate_url,
)

logger = structlog.get_logger(__name__)

USER_AGENT = (
    "wolf-research/0.1 (Wazuh security assistant; +https://github.com/alsechemist/project-wolf)"
)

_MAX_REDIRECT_HOPS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# Text-ish content types Wolf will read (§4). Anything else — images,
# archives, executables, PDFs — is refused, never parsed or persisted.
_ALLOWED_CONTENT_PREFIXES = ("text/",)
_ALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "application/xhtml+xml",
        "application/xml",
        "application/rss+xml",
        "application/atom+xml",
    }
)
_HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})


class WebFetchError(WolfError):
    """A page could not be fetched safely — surfaced honestly to the model."""

    http_status = 502
    error_code = "web_fetch_error"


@dataclass
class FetchedPage:
    """One safely fetched, extracted page."""

    url: str  # the URL that was requested (hostname form)
    final_url: str  # after redirects (hostname form)
    title: str
    text: str
    content_type: str
    truncated: bool
    links: list[str] = field(default_factory=list)


def _content_type_allowed(content_type: str) -> bool:
    return content_type.startswith(_ALLOWED_CONTENT_PREFIXES) or (
        content_type in _ALLOWED_CONTENT_TYPES
    )


class WebFetcher:
    """The one guarded fetch path. Owns an httpx client; close via `aclose()`."""

    def __init__(
        self,
        *,
        max_bytes: int,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self._max_bytes = max_bytes
        self._timeout_seconds = timeout_seconds
        self._resolver = resolver
        # follow_redirects stays False — every hop is re-validated manually.
        self._client = client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, raw_url: str) -> FetchedPage:
        """Fetch one page through the full guard stack.

        Raises :class:`WebUrlError` (URL/SSRF rejection) or
        :class:`WebFetchError` (transport/content failure) with a message the
        model can relay honestly.
        """
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await self._fetch_following_redirects(raw_url)
        except TimeoutError as exc:
            raise WebFetchError(
                f"Fetch of {raw_url} exceeded the {self._timeout_seconds:.0f}s deadline"
            ) from exc

    async def _fetch_following_redirects(self, raw_url: str) -> FetchedPage:
        original = validate_url(raw_url)
        current = original
        for _hop in range(_MAX_REDIRECT_HOPS + 1):
            response, body, truncated = await self._request_pinned(current)
            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise WebFetchError(
                        f"{current.url} answered HTTP {response.status_code} with no Location"
                    )
                # Re-validate + re-resolve the next hop from scratch — a
                # redirect is just another untrusted URL (§1).
                current = validate_url(str(httpx.URL(current.url).join(location)))
                continue
            if response.status_code != 200:
                raise WebFetchError(f"{current.url} answered HTTP {response.status_code}")
            return self._extract(original, current, response, body, truncated)
        raise WebFetchError(f"{raw_url} redirected more than {_MAX_REDIRECT_HOPS} times")

    async def _request_pinned(self, validated: ValidatedUrl) -> tuple[httpx.Response, bytes, bool]:
        """One request, connected to the vetted IP, streaming under the byte cap."""
        pinned_ip = await resolve_pinned_ip(validated, resolver=self._resolver)
        ip_authority = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
        default_port = 443 if validated.scheme == "https" else 80
        host_header = (
            validated.host
            if validated.port == default_port
            else f"{validated.host}:{validated.port}"
        )
        request_url = (
            f"{validated.scheme}://{ip_authority}:{validated.port}{validated.path_and_query}"
        )
        headers = {
            "Host": host_header,
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html, application/xhtml+xml, text/plain, application/json;q=0.9, */*;q=0.1"
            ),
        }
        # `sni_hostname` makes TLS handshake + certificate verification use
        # the real hostname even though the socket dials the pinned IP.
        extensions = {"sni_hostname": validated.host} if validated.scheme == "https" else {}

        chunks: list[bytes] = []
        received = 0
        truncated = False
        try:
            async with self._client.stream(
                "GET", request_url, headers=headers, extensions=extensions
            ) as response:
                if response.status_code == 200:
                    content_type = (
                        response.headers.get("content-type", "").split(";")[0].strip().lower()
                    )
                    if content_type and not _content_type_allowed(content_type):
                        raise WebFetchError(
                            f"{validated.url} has content type {content_type!r} — "
                            "only text-like pages are fetched"
                        )
                    # aiter_bytes yields DECODED (decompressed) bytes, so this
                    # cap bounds post-inflation size — the bomb guard (§4).
                    async for chunk in response.aiter_bytes():
                        received += len(chunk)
                        if received > self._max_bytes:
                            overshoot = received - self._max_bytes
                            chunks.append(chunk[: len(chunk) - overshoot])
                            truncated = True
                            break
                        chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise WebFetchError(f"Fetch of {validated.url} failed: {exc}") from exc
        return response, b"".join(chunks), truncated

    def _extract(
        self,
        original: ValidatedUrl,
        final: ValidatedUrl,
        response: httpx.Response,
        body: bytes,
        truncated: bool,
    ) -> FetchedPage:
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        text = body.decode(response.charset_encoding or "utf-8", errors="replace")
        if truncated:
            logger.info(
                "web_fetch_truncated",
                url=final.url,
                cap_bytes=self._max_bytes,
            )
        # No content-type header → best-effort HTML (docs servers are
        # occasionally sloppy); the byte cap already bounds the damage.
        if not content_type or content_type in _HTML_CONTENT_TYPES:
            page = extract_html(text, base_url=final.url)
            return FetchedPage(
                url=original.url,
                final_url=final.url,
                title=page.title,
                text=page.text,
                content_type=content_type or "text/html",
                truncated=truncated,
                links=page.links,
            )
        return FetchedPage(
            url=original.url,
            final_url=final.url,
            title="",
            text=sanitize_text(text),
            content_type=content_type,
            truncated=truncated,
            links=[],
        )
