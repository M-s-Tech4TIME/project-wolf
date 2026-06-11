"""Unit tests for the Phase 5.6-c mTLS middleware.

Builds a tiny Starlette test app with MtlsMiddleware mounted and
hits it with the standard FastAPI TestClient. Peer-cert info is
injected by overriding the relevant scope key in a wrapper ASGI
app, mimicking what `wolf_server.runtime.peer_cert_patch` does at
production runtime.

What's covered:
* No peer cert presented → 401 with `mtls_required` error code.
* Peer cert with disallowed CN → 401 with `mtls_cn_rejected`.
* Peer cert with allowed CN → request passes, downstream handler runs.
* GET /healthz from loopback (127.0.0.1) → bypasses cert check.
* GET /healthz from a NON-loopback address → still requires cert.
* Non-GET /healthz → still requires cert (the bypass is GET-only).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from wolf_server.auth.mtls_middleware import MtlsMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# Type alias for the synthesised "peer cert dict" Python's ssl module
# returns from `SSLSocket.getpeercert()` — a tuple of RDN tuples.
_PeerCert = dict[str, Any]


def _make_cert(cn: str) -> _PeerCert:
    """Build a minimal parsed-cert dict shaped like ssl.getpeercert()."""
    return {"subject": ((("commonName", cn),),)}


def _build_app(
    *,
    allowed_cns: list[str],
    peer_cert: _PeerCert | None,
    client_host: str = "10.0.0.5",
) -> TestClient:
    """Spin up a one-route test app with MtlsMiddleware applied,
    plus a stub upstream ASGI wrapper that injects peer_cert + client
    info into scope (mimicking the production peer-cert monkey-patch).
    """
    app = FastAPI()

    @app.get("/api/v1/test")
    async def _ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/healthz")
    async def _healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(MtlsMiddleware, allowed_cns=allowed_cns)

    # Wrap the app so we can inject peer_cert into scope BEFORE
    # MtlsMiddleware runs (which is the same position
    # peer_cert_patch.py occupies in production: inside the ASGI
    # cycle's __init__, before any middleware dispatches).
    inner_app = app

    async def scoped_app(
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") == "http":
            scope.setdefault("state", {})
            if peer_cert is not None:
                scope["state"]["wolf_peer_cert"] = peer_cert
            # Override the client tuple so the /healthz bypass tests
            # can simulate loopback vs. non-loopback callers.
            scope["client"] = (client_host, 12345)
        await inner_app(scope, receive, send)

    return TestClient(scoped_app, raise_server_exceptions=True)


# ─── reject paths ──────────────────────────────────────────────────────────


def test_rejects_request_with_no_peer_cert() -> None:
    """When no client cert is presented, the middleware returns 401
    with the `mtls_required` error code."""
    client = _build_app(allowed_cns=["wolf-dashboard-client"], peer_cert=None)
    resp = client.get("/api/v1/test")
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"] == "mtls_required"


def test_rejects_request_with_disallowed_cn() -> None:
    """A cert with a CN that isn't in the allowlist returns 401 with
    the `mtls_cn_rejected` error code."""
    client = _build_app(
        allowed_cns=["wolf-dashboard-client"],
        peer_cert=_make_cert("attacker.example.com"),
    )
    resp = client.get("/api/v1/test")
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"] == "mtls_cn_rejected"
    assert "attacker.example.com" in body["detail"]


def test_rejects_cert_without_common_name() -> None:
    """A degenerate cert whose subject has no commonName attribute is
    treated as 'no cert presented' (the CN is what the allowlist keys
    on)."""
    weird = {"subject": ((("organizationName", "Some Org"),),)}
    client = _build_app(
        allowed_cns=["wolf-dashboard-client"],
        peer_cert=weird,
    )
    resp = client.get("/api/v1/test")
    assert resp.status_code == 401
    assert resp.json()["error"] == "mtls_required"


# ─── accept paths ──────────────────────────────────────────────────────────


def test_accepts_request_with_allowed_cn() -> None:
    """A cert whose CN matches the allowlist passes the gate; the
    downstream handler runs and returns its real response."""
    client = _build_app(
        allowed_cns=["wolf-dashboard-client"],
        peer_cert=_make_cert("wolf-dashboard-client"),
    )
    resp = client.get("/api/v1/test")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_allowlist_supports_multiple_cns() -> None:
    """Future relay daemons get their own CN added to the env var;
    confirm the middleware honors the full list."""
    client = _build_app(
        allowed_cns=["wolf-dashboard-client", "wolf-relay-acme"],
        peer_cert=_make_cert("wolf-relay-acme"),
    )
    resp = client.get("/api/v1/test")
    assert resp.status_code == 200


# ─── /healthz bypass ──────────────────────────────────────────────────────


@pytest.mark.parametrize("loopback", ["127.0.0.1", "::1"])
def test_healthz_from_loopback_bypasses_cert_check(loopback: str) -> None:
    """Kubernetes liveness probes, systemd watchdog scripts, and
    same-host curl all hit /healthz without a Wolf client cert. This
    must succeed because operators rely on it for liveness."""
    client = _build_app(
        allowed_cns=["wolf-dashboard-client"],
        peer_cert=None,
        client_host=loopback,
    )
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_healthz_from_non_loopback_still_requires_cert() -> None:
    """The loopback exception can't be exploited by a LAN attacker —
    /healthz from an external address still requires the cert."""
    client = _build_app(
        allowed_cns=["wolf-dashboard-client"],
        peer_cert=None,
        client_host="192.168.1.50",
    )
    resp = client.get("/healthz")
    assert resp.status_code == 401


def test_non_get_healthz_still_requires_cert() -> None:
    """The bypass is GET-only — POST /healthz (which doesn't exist
    on the real wolf-server but a probing client could try) demands
    the cert. Belt-and-braces: keeps the bypass surface as narrow as
    possible."""
    client = _build_app(
        allowed_cns=["wolf-dashboard-client"],
        peer_cert=None,
        client_host="127.0.0.1",
    )
    resp = client.post("/healthz")
    # /healthz only has a GET handler, so the bypass-or-cert decision
    # happens before we reach the handler. Without the bypass the
    # mTLS middleware rejects with 401.
    assert resp.status_code == 401
