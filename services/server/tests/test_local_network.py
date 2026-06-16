"""Unit tests for the same-network gate's IP machinery (Phase 6.5-h.2).

Covers the two pure pieces the gate is built from:

* ``wolf_server.network.local_network`` — NIC-CIDR enumeration + membership.
* ``wolf_server.api.auth._resolve_gate_client_ip`` — the trust rule that
  decides whether the dashboard-propagated ``X-Wolf-Client-IP`` header is
  honoured (only under mTLS) or the real TCP peer is used instead.

These need no app / DB; the end-to-end gate behaviour (403 + token
preservation + audit) lives in test_rbac.py alongside the verify fixtures.
"""

from __future__ import annotations

import ipaddress

import pytest
from wolf_server.api.auth import _resolve_gate_client_ip
from wolf_server.network import local_network as ln

# ── local_cidrs / membership ─────────────────────────────────────────────────


def test_local_cidrs_always_includes_loopback() -> None:
    cidrs = ln.local_cidrs()
    assert ipaddress.ip_network("127.0.0.0/8") in cidrs
    assert ipaddress.ip_network("::1/128") in cidrs


def test_loopback_is_always_in_local_network() -> None:
    assert ln.client_ip_in_local_network("127.0.0.1") is True
    assert ln.client_ip_in_local_network("::1") is True


def test_ipv4_mapped_ipv6_is_normalised() -> None:
    # A dual-stack listener reports an IPv4 client as ::ffff:a.b.c.d; it must
    # still match the IPv4 loopback CIDR.
    assert ln.client_ip_in_local_network("::ffff:127.0.0.1") is True


@pytest.mark.parametrize("bad", [None, "", "not-an-ip", "testclient", "999.1.1.1"])
def test_unparseable_input_fails_closed(bad: str | None) -> None:
    assert ln.client_ip_in_local_network(bad) is False


def test_membership_against_fixed_cidrs(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pin the network set so the assertion doesn't depend on the host's real
    # interfaces (CI runners have unpredictable IPs).
    fixed = (ipaddress.ip_network("10.42.0.0/16"),)
    monkeypatch.setattr(ln, "local_cidrs", lambda: fixed)

    assert ln.client_ip_in_local_network("10.42.7.9") is True
    assert ln.client_ip_in_local_network("203.0.113.5") is False  # TEST-NET-3
    # Loopback is NOT in the pinned set → proves membership is the real test,
    # not a hard-coded loopback pass.
    assert ln.client_ip_in_local_network("127.0.0.1") is False


def test_normalise_strips_zone_id_and_v4mapped() -> None:
    assert ln._normalise("::ffff:192.0.2.1") == "192.0.2.1"
    assert ln._normalise("fe80::1%eth0") == "fe80::1"
    assert ln._normalise(" 10.0.0.1 ") == "10.0.0.1"


def test_enumerate_nic_cidrs_does_not_raise() -> None:
    # Best-effort enumeration against whatever NICs exist; must return a list.
    assert isinstance(ln._enumerate_nic_cidrs(), list)


# ── trust rule: when is X-Wolf-Client-IP honoured? ───────────────────────────


def test_header_honoured_only_under_mtls() -> None:
    # mTLS-authenticated dashboard + header present → trust the header.
    assert (
        _resolve_gate_client_ip(
            mtls_cert_cn="wolf-dashboard-client",
            header_ip="192.168.1.50",
            source_ip="127.0.0.1",
        )
        == "192.168.1.50"
    )


def test_spoofed_header_ignored_without_mtls() -> None:
    # No mTLS trust → the header is a forgery and must be ignored in favour
    # of the real TCP peer.
    assert (
        _resolve_gate_client_ip(
            mtls_cert_cn=None,
            header_ip="8.8.8.8",
            source_ip="127.0.0.1",
        )
        == "127.0.0.1"
    )


def test_missing_header_falls_back_to_peer_under_mtls() -> None:
    # mTLS trusted but the edge proxy didn't stamp the header (degraded /
    # older dashboard) → fall back to the peer, don't lock everyone out.
    assert (
        _resolve_gate_client_ip(
            mtls_cert_cn="wolf-dashboard-client",
            header_ip=None,
            source_ip="10.0.0.9",
        )
        == "10.0.0.9"
    )


def test_no_header_no_mtls_uses_peer() -> None:
    assert (
        _resolve_gate_client_ip(mtls_cert_cn=None, header_ip=None, source_ip="127.0.0.1")
        == "127.0.0.1"
    )
