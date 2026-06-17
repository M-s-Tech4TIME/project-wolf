"""Unit tests for wolf_server.wazuh.probe — Phase 6.6-a.

The probes are exercised against an in-process ``httpx.MockTransport`` (no
network), so they run in every environment with no skips.  A probe never
raises on an HTTP/transport failure — it captures the outcome so the caller
decides hard-vs-soft.
"""

import httpx
from wolf_server.wazuh.probe import (
    probe_dashboard,
    probe_indexer,
    probe_indexer_read,
    probe_manager_api,
)


def _client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


# ── Indexer ──────────────────────────────────────────────────────────────────


async def test_probe_indexer_ok_200() -> None:
    async with _client(lambda req: httpx.Response(200, json={})) as c:
        r = await probe_indexer("https://idx:9200", "u", "p", verify_tls=False, client=c)
    assert r.ok is True
    assert r.role == "indexer"
    assert r.status_code == 200


async def test_probe_indexer_tolerates_403() -> None:
    # A read-only role without cluster-monitor still authenticated → ok.
    async with _client(lambda req: httpx.Response(403)) as c:
        r = await probe_indexer("https://idx:9200", "u", "p", verify_tls=False, client=c)
    assert r.ok is True
    assert r.status_code == 403


async def test_probe_indexer_401_is_bad_creds() -> None:
    async with _client(lambda req: httpx.Response(401)) as c:
        r = await probe_indexer("https://idx:9200", "u", "bad", verify_tls=False, client=c)
    assert r.ok is False
    assert r.status_code == 401
    assert "rejected" in r.detail.lower()


async def test_probe_indexer_unexpected_status_fails() -> None:
    async with _client(lambda req: httpx.Response(500)) as c:
        r = await probe_indexer("https://idx:9200", "u", "p", verify_tls=False, client=c)
    assert r.ok is False
    assert r.status_code == 500


async def test_probe_indexer_unreachable_fails_closed() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated", request=req)

    async with _client(boom) as c:
        r = await probe_indexer("https://idx:9200", "u", "p", verify_tls=False, client=c)
    assert r.ok is False
    assert r.status_code is None
    assert "unreachable" in r.detail.lower()


# ── Indexer read probe (per-org credential — Phase 6.6-f) ───────────────────


async def test_probe_indexer_read_ok_reports_count() -> None:
    async with _client(lambda req: httpx.Response(200, json={"count": 27})) as c:
        r = await probe_indexer_read(
            "https://idx:9200", "u", "p", "wazuh-alerts-*", verify_tls=False, client=c
        )
    assert r.ok is True
    assert r.status_code == 200
    assert "27 alert(s)" in r.detail
    assert "wazuh-alerts-*" in r.detail


async def test_probe_indexer_read_403_is_denied_not_ok() -> None:
    # The key fix: a 403 on the index is a *failure*, not "authenticated".
    async with _client(lambda req: httpx.Response(403)) as c:
        r = await probe_indexer_read(
            "https://idx:9200", "u", "p", "wazuh-alerts-*", verify_tls=False, client=c
        )
    assert r.ok is False
    assert r.status_code == 403
    assert "denied read" in r.detail.lower()


async def test_probe_indexer_read_401_is_bad_creds() -> None:
    async with _client(lambda req: httpx.Response(401)) as c:
        r = await probe_indexer_read(
            "https://idx:9200", "u", "bad", "wazuh-alerts-*", verify_tls=False, client=c
        )
    assert r.ok is False
    assert r.status_code == 401
    assert "rejected" in r.detail.lower()


async def test_probe_indexer_read_unreachable_fails_closed() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated", request=req)

    async with _client(boom) as c:
        r = await probe_indexer_read(
            "https://idx:9200", "u", "p", "wazuh-alerts-*", verify_tls=False, client=c
        )
    assert r.ok is False
    assert r.status_code is None
    assert "unreachable" in r.detail.lower()


# ── Manager Server API ─────────────────────────────────────────────────────


async def test_probe_manager_ok_200() -> None:
    async with _client(lambda req: httpx.Response(200, json={"data": {"token": "x"}})) as c:
        r = await probe_manager_api(
            "https://mgr:55000", "wazuh-wui", "p", verify_tls=False, client=c
        )
    assert r.ok is True
    assert r.role == "manager"
    assert r.status_code == 200


async def test_probe_manager_401_is_bad_creds() -> None:
    async with _client(lambda req: httpx.Response(401)) as c:
        r = await probe_manager_api("https://mgr:55000", "wrong", "p", verify_tls=False, client=c)
    assert r.ok is False
    assert r.status_code == 401
    assert "own user database" in r.detail.lower()


async def test_probe_manager_unexpected_status_fails() -> None:
    async with _client(lambda req: httpx.Response(404)) as c:
        r = await probe_manager_api("https://mgr:55000", "u", "p", verify_tls=False, client=c)
    assert r.ok is False
    assert r.status_code == 404


# ── Dashboard (unauthenticated reachability) ────────────────────────────────


async def test_probe_dashboard_200_ok() -> None:
    async with _client(lambda req: httpx.Response(200)) as c:
        r = await probe_dashboard("https://dash", verify_tls=False, client=c)
    assert r.ok is True
    assert r.role == "dashboard"


async def test_probe_dashboard_redirect_is_reachable() -> None:
    # The Wazuh dashboard commonly 302s to its login page — still reachable.
    async with _client(lambda req: httpx.Response(302, headers={"location": "/login"})) as c:
        r = await probe_dashboard("https://dash", verify_tls=False, client=c)
    assert r.ok is True
    assert r.status_code == 302


async def test_probe_dashboard_5xx_fails() -> None:
    async with _client(lambda req: httpx.Response(503)) as c:
        r = await probe_dashboard("https://dash", verify_tls=False, client=c)
    assert r.ok is False
    assert r.status_code == 503


async def test_probe_dashboard_unreachable_fails_closed() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated", request=req)

    async with _client(boom) as c:
        r = await probe_dashboard("https://dash", verify_tls=False, client=c)
    assert r.ok is False
    assert r.status_code is None
