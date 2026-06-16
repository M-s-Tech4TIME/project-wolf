"""Local-network CIDR enumeration for the same-network verification gate.

Phase 6.5-h.2 (ADR 0018 item 9). The invite-verification gate in
``api/auth.py`` (``verify-invite``) only lets a user flip to ``verified``
when their browser sits inside Wolf's own network. wolf-server is the
authority on what "Wolf's network" means: it enumerates the CIDRs of its
own network interfaces and checks the real client IP — propagated by the
dashboard edge proxy over mTLS as ``X-Wolf-Client-IP`` — against them.

Loopback is always trusted so a co-located / all-in-one deployment (the
operator's browser on the same host as wolf-server) verifies cleanly.

The real-IP plumbing lives in the dashboard tier (a TLS edge proxy owns
the browser's TCP socket — Next 16 hides it from route handlers and its
``X-Forwarded-For`` is client-spoofable). This module is only the
"is this IP one of mine?" half.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache

import ifaddr
import structlog

logger = structlog.get_logger(__name__)

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# Always-trusted networks: loopback covers co-located / all-in-one
# deployments where the operator's browser shares the host with
# wolf-server (the request arrives from 127.0.0.1 / ::1).
_LOOPBACK_CIDRS: tuple[IPNetwork, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)


def _enumerate_nic_cidrs() -> list[IPNetwork]:
    """The CIDR of every address on every local NIC (best-effort).

    Skips any address ifaddr/ipaddress can't make sense of rather than
    failing the whole enumeration — a single odd interface must not be
    able to brick onboarding.
    """
    cidrs: list[IPNetwork] = []
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            # ifaddr.IP.ip is a plain str for IPv4 and a
            # (address, flowinfo, scope_id) tuple for IPv6.
            addr = ip.ip[0] if isinstance(ip.ip, tuple) else ip.ip
            try:
                network = ipaddress.ip_network(
                    f"{addr}/{ip.network_prefix}", strict=False
                )
            except ValueError:
                continue
            cidrs.append(network)
    return cidrs


@lru_cache(maxsize=1)
def local_cidrs() -> tuple[IPNetwork, ...]:
    """Wolf's own network CIDRs (NIC networks + loopback), deduped + cached.

    Cached for the process lifetime — NICs rarely change while a server
    is running, and enumerating them per request would be wasteful. A
    future slice can add a refresh hook / TTL if hot re-homing turns out
    to be a real deployment case.
    """
    seen: dict[IPNetwork, None] = {}
    for net in (*_enumerate_nic_cidrs(), *_LOOPBACK_CIDRS):
        seen[net] = None
    cidrs = tuple(seen)
    logger.info("local_cidrs_enumerated", cidrs=[str(c) for c in cidrs])
    return cidrs


def _normalise(ip: str) -> str:
    """Coerce a raw remote-address string into something ``ip_address`` parses.

    * Strips an IPv4-mapped IPv6 prefix (``::ffff:192.0.2.1`` → ``192.0.2.1``)
      so a dual-stack listener's view of an IPv4 client still compares
      against IPv4 NIC CIDRs.
    * Drops a zone id (``fe80::1%eth0`` → ``fe80::1``), which ``ip_address``
      cannot parse.
    """
    s = ip.strip()
    if s.lower().startswith("::ffff:") and "." in s:
        return s[len("::ffff:") :]
    if "%" in s:
        s = s.split("%", 1)[0]
    return s


def client_ip_in_local_network(ip: str | None) -> bool:
    """True iff ``ip`` falls within one of Wolf's own network CIDRs.

    Returns ``False`` for ``None`` / unparseable input: the gate fails
    closed (an IP we can't understand is treated as out-of-network).
    """
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(_normalise(ip))
    except ValueError:
        return False
    # IPv4Address-in-IPv6Network (and vice-versa) is False, not an error.
    return any(addr in net for net in local_cidrs())
