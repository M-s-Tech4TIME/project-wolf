"""mTLS middleware — Phase 5.6-c.

Enforces that incoming TLS connections present a client cert whose
Subject CN appears in the operator-configured allowlist. Two layers
work together:

1. **TLS-layer** (uvicorn ``ssl_cert_reqs=ssl.CERT_OPTIONAL``):
   the handshake accepts the connection regardless of client cert,
   but if a cert IS presented uvicorn verifies it against
   ``ssl_ca_certs`` (the Wolf CA). A cert signed by any OTHER CA is
   rejected at handshake — the middleware never sees it.
2. **Application-layer** (this middleware): reads the verified peer
   cert's Subject CN, rejects anything not in the allowlist with a
   JSON 401, audit-logs every reject decision.

Why CERT_OPTIONAL rather than CERT_REQUIRED:
* lets ``/healthz`` from loopback bypass the cert check, so simple
  ops tools (Kubernetes liveness probes, systemd watchdog scripts)
  can probe wolf-server without distributing the dashboard-client
  cert;
* returns useful JSON 401 responses instead of bare TLS errors,
  so the audit trail records WHY a client was rejected;
* keeps the dev no-certs path unchanged (the launcher only
  configures mTLS when the Wolf CA + server leaf both exist).

The peer cert is read from ``scope["state"]["wolf_peer_cert"]``,
which is populated by the monkey-patch in
``wolf_server.runtime.peer_cert_patch``. If that key is absent,
this middleware treats it as "no cert presented."
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from fastapi import Request, Response
    from starlette.middleware.base import RequestResponseEndpoint

logger = structlog.get_logger(__name__)

# Loopback addresses that may probe /healthz without a client cert.
_LOOPBACK_IPS: frozenset[str] = frozenset({"127.0.0.1", "::1"})
_HEALTHZ_PATH = "/healthz"


def _peer_cert_cn(peer_cert: dict[str, Any] | None) -> str | None:
    """Extract the Subject CN from a parsed peer cert dict.

    Python's ``ssl.SSLSocket.getpeercert()`` returns a dict shaped
    like ``{"subject": ((("commonName", "X"),),)}`` — a tuple of
    RDN tuples, each containing (attribute, value) pairs. We pull
    the first commonName encountered. Returns None if the cert is
    missing, empty, or has no CN.
    """
    if not peer_cert:
        return None
    subject = peer_cert.get("subject", ())
    for rdn in subject:
        for attr, value in rdn:
            if attr == "commonName":
                return str(value)
    return None


class MtlsMiddleware(BaseHTTPMiddleware):
    """Enforce the client-cert CN allowlist.

    Only mounted when ``Settings.mtls_enabled`` is True (i.e. the Wolf
    CA + server leaf exist on disk). When mTLS is off, this middleware
    is not in the chain at all and every request passes through to
    AuthMiddleware as before.
    """

    def __init__(self, app: Any, *, allowed_cns: list[str]) -> None:  # noqa: ANN401
        super().__init__(app)
        self._allowed_cns: frozenset[str] = frozenset(allowed_cns)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        # Bypass: GET /healthz from loopback. Lets Kubernetes
        # liveness probes / systemd watchdog scripts / `curl` from
        # the same box probe wolf-server without distributing the
        # dashboard-client cert. Non-loopback /healthz still needs
        # a valid cert, so the bypass can't be abused over the LAN.
        if request.method == "GET" and request.url.path == _HEALTHZ_PATH:
            client = request.client
            if client is not None and client.host in _LOOPBACK_IPS:
                return await call_next(request)

        peer_cert = request.scope.get("state", {}).get("wolf_peer_cert")
        cn = _peer_cert_cn(peer_cert)

        if cn is None:
            # No client cert OR cert had no CN. Reject.
            logger.warning(
                "mtls_reject",
                reason="no_client_cert",
                client=request.client.host if request.client else None,
                method=request.method,
                path=request.url.path,
            )
            return JSONResponse(
                status_code=401,
                content={
                    "error": "mtls_required",
                    "detail": (
                        "wolf-server requires a Wolf-CA-signed client "
                        "certificate. Run `wolf-cert init` on the caller's "
                        "host and present the resulting client cert."
                    ),
                },
            )

        if cn not in self._allowed_cns:
            logger.warning(
                "mtls_reject",
                reason="cn_not_allowed",
                cert_cn=cn,
                allowed_cns=sorted(self._allowed_cns),
                client=request.client.host if request.client else None,
                method=request.method,
                path=request.url.path,
            )
            return JSONResponse(
                status_code=401,
                content={
                    "error": "mtls_cn_rejected",
                    "detail": (
                        f"client cert CN {cn!r} is not in the allowlist. "
                        f"Operator must add it to MTLS_ALLOWED_CLIENT_CNS "
                        f"to permit this caller."
                    ),
                },
            )

        # Stash the CN on request.state so downstream code can
        # audit-log "which component made this call" if it wants.
        request.state.mtls_cert_cn = cn
        return await call_next(request)
