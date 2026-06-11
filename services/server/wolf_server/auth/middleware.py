"""Auth middleware — validates JWT from cookie and populates request.state.session.

This middleware runs on every request.  Unauthenticated requests leave
`request.state.session` empty; the `require_organization_context` dependency
then returns HTTP 401.

Phase 6.5-g: after JWT validation the session blacklist is consulted —
a revoked session (logout, force-revoke, password reset) gets 401
immediately even though the token signature and expiry are still valid.

Cookie name: `wolf_access_token` (HTTP-only, Secure in production, SameSite=Lax).
"""

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from wolf_common.errors import AuthenticationError, SessionExpiredError

from wolf_server.auth.blacklist import get_session_blacklist
from wolf_server.auth.local import decode_access_token

# Paths that do not require authentication.
_PUBLIC_PATHS: frozenset[str] = frozenset(
    {"/healthz", "/api/v1/auth/login", "/api/v1/auth/oidc/callback", "/docs", "/openapi.json"}
)

COOKIE_NAME = "wolf_access_token"


def _reject(status_detail: str) -> Response:
    resp = JSONResponse(status_code=401, content={"detail": status_detail})
    resp.delete_cookie(COOKIE_NAME)
    return resp


class AuthMiddleware(BaseHTTPMiddleware):
    """Extract and validate the access token cookie; populate request.state.session."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip auth for public paths.
        if request.url.path in _PUBLIC_PATHS or request.url.path.startswith("/docs"):
            request.state.session = {}
            return await call_next(request)

        token = request.cookies.get(COOKIE_NAME)
        if not token:
            # No cookie — leave session empty; protected routes return 401 via dependency.
            request.state.session = {}
            return await call_next(request)

        try:
            payload = decode_access_token(token)
        except SessionExpiredError:
            return _reject("Session expired. Please log in again.")
        except AuthenticationError:
            return _reject("Invalid session token.")

        # Phase 6.5-g: server-side revocation check. The factory returns the
        # process-wide singleton shared with the trigger sites (logout,
        # password reset, force-revoke), so a revocation is visible here on
        # the very next request.
        session_id = payload.get("session_id")
        user_id = payload.get("sub")
        if session_id and user_id:
            revoked = await get_session_blacklist().is_revoked(
                str(session_id), str(user_id), float(payload.get("iat", 0))
            )
            if revoked:
                return _reject("Session revoked. Please log in again.")

        request.state.session = {
            "user_id": user_id,
            "organization_id": payload.get("organization_id"),
            "role": payload.get("role"),
            "session_id": session_id,
            # iat/exp (epoch seconds) carried so trigger sites can compute
            # blacklist TTLs that match the token's remaining lifetime.
            "iat": payload.get("iat"),
            "exp": payload.get("exp"),
        }
        return await call_next(request)
