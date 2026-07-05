"""URL validation + SSRF guard for the web-research fetch path (ADR 0032 A6 §1, §10).

Every URL the fetcher touches — model-supplied, user-supplied, a search hit,
a redirect Location, a crawl-discovered link — passes through here first.
No URL is trusted because a search returned it.

Two layers:

1. **Syntactic validation** (`validate_url`): scheme allowlist (http/https
   only — no file/ftp/gopher/data/javascript), no credentials-in-URL, a
   present + punycode-normalizable host, a sane port. Rejects before any
   network I/O.
2. **Resolution pinning** (`resolve_pinned_ip`): resolve the host and vet
   EVERY address it resolves to against the blocked ranges — loopback,
   RFC-1918 private, link-local (v4 `169.254/16` incl. cloud metadata, v6
   `fe80::/10`), ULA (`fc00::/7`, incl. `fd00:ec2::254`), multicast,
   reserved, unspecified (`0.0.0.0` / `::`) — then return one vetted IP the
   caller must CONNECT to directly. Connecting to the pinned IP (not the
   hostname) defeats DNS rebinding: the address that was validated is the
   address that is dialed. Decimal/octal/hex IP encodings need no special
   casing — `getaddrinfo` resolves them to the same numeric address, which
   the post-resolution check vets like any other.

The resolver is injectable so tests never do live DNS (hermetic CI).
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from wolf_common.errors import WolfError

# An injected resolver maps (host, port) -> list of IP address strings.
Resolver = Callable[[str, int], Awaitable[list[str]]]

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class WebUrlError(WolfError):
    """A URL failed validation or resolved to a forbidden address.

    The message is model-facing (it comes back as the tool error), so it is
    specific about WHAT was rejected but never invites a workaround.
    """

    http_status = 422
    error_code = "web_url_rejected"


@dataclass(frozen=True)
class ValidatedUrl:
    """A syntactically vetted URL, decomposed for the fetch path."""

    url: str  # the original (normalized-host) absolute URL
    scheme: str
    host: str  # punycode-normalized, lower-case
    port: int  # explicit or scheme default
    path_and_query: str  # everything after the authority, always starts "/"


def validate_url(raw_url: str) -> ValidatedUrl:
    """Syntactic SSRF/transport checks — no network I/O.

    Raises :class:`WebUrlError` with a specific reason on any violation.
    """
    raw_url = raw_url.strip()
    if not raw_url:
        raise WebUrlError("Empty URL")
    try:
        parts = urlsplit(raw_url)
    except ValueError as exc:
        raise WebUrlError(f"Unparseable URL: {exc}") from exc

    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise WebUrlError(f"URL scheme {scheme or '(none)'!r} is not allowed — only http/https")
    if parts.username is not None or parts.password is not None:
        raise WebUrlError("URLs with embedded credentials (user:pass@host) are not allowed")

    hostname = parts.hostname
    if not hostname:
        raise WebUrlError("URL has no host")
    # Punycode-normalize (defeats IDN/homograph tricks against the domain
    # allowlist — A6 §10). `encode('idna')` rejects malformed labels.
    try:
        host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise WebUrlError(f"URL host {hostname!r} is not a valid domain name") from exc

    try:
        port = parts.port  # raises ValueError for out-of-range ports
    except ValueError as exc:
        raise WebUrlError("URL port is invalid") from exc
    if port is None:
        port = 443 if scheme == "https" else 80

    path = parts.path or "/"
    path_and_query = f"{path}?{parts.query}" if parts.query else path
    return ValidatedUrl(
        url=raw_url,
        scheme=scheme,
        host=host,
        port=port,
        path_and_query=path_and_query,
    )


def _check_ip_allowed(address: str, *, host: str) -> None:
    """Reject any address outside the global-unicast internet."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as exc:
        raise WebUrlError(f"Host {host!r} resolved to a non-IP address {address!r}") from exc
    # Unwrap IPv4-mapped IPv6 (`::ffff:127.0.0.1`) so the mapped IPv4 rules
    # apply — the classic mapped-loopback bypass.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # `is_global` is False for almost every forbidden class in one check:
    # loopback, RFC-1918 private, link-local (incl. 169.254.169.254 metadata),
    # ULA (incl. fd00:ec2::254), reserved, unspecified, benchmarking, CGNAT.
    # IPv4 multicast (224/4) is nonetheless `is_global=True` in CPython's
    # IANA-registry reading, so reject it explicitly.
    if not ip.is_global or ip.is_multicast:
        raise WebUrlError(
            f"Host {host!r} resolves to {ip} — a private/loopback/link-local/"
            "reserved address Wolf will not fetch (SSRF guard)"
        )


async def _default_resolver(host: str, port: int) -> list[str]:
    """DNS resolution via the event loop's executor-backed getaddrinfo."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebUrlError(f"Host {host!r} does not resolve: {exc}") from exc
    return [str(info[4][0]) for info in infos]


async def resolve_pinned_ip(
    validated: ValidatedUrl,
    *,
    resolver: Resolver | None = None,
) -> str:
    """Resolve the host and return ONE vetted IP to connect to.

    EVERY resolved address is checked — a host is rejected if ANY of its
    addresses is forbidden (a half-poisoned record must not be fetchable by
    resolution luck). The caller must connect to the returned IP directly
    (pinning), and must call this again after every redirect hop.
    """
    resolve = resolver or _default_resolver
    addresses = await resolve(validated.host, validated.port)
    if not addresses:
        raise WebUrlError(f"Host {validated.host!r} resolved to no addresses")
    for address in addresses:
        _check_ip_allowed(address, host=validated.host)
    # Prefer IPv4 for the widest connectivity; fall back to the first entry.
    for address in addresses:
        if ":" not in address:
            return address
    return addresses[0]
