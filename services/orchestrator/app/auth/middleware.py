"""Auth middleware — validates JWT from cookie and populates request.state.session.

This middleware runs on every request.  Unauthenticated requests leave
`request.state.session` empty; the `require_tenant_context` dependency
then returns HTTP 401.

Cookie name: `wolf_access_token` (HTTP-only, Secure in production, SameSite=Lax).
"""

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from wolf_common.errors import AuthenticationError, SessionExpiredError

from app.auth.local import decode_access_token

# Paths that do not require authentication.
_PUBLIC_PATHS: frozenset[str] = frozenset(
    {"/healthz", "/api/v1/auth/login", "/api/v1/auth/oidc/callback", "/docs", "/openapi.json"}
)

COOKIE_NAME = "wolf_access_token"


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
            resp = JSONResponse(
                status_code=401, content={"detail": "Session expired. Please log in again."}
            )
            resp.delete_cookie(COOKIE_NAME)
            return resp
        except AuthenticationError:
            resp = JSONResponse(status_code=401, content={"detail": "Invalid session token."})
            resp.delete_cookie(COOKIE_NAME)
            return resp

        request.state.session = {
            "user_id": payload.get("sub"),
            "tenant_id": payload.get("tenant_id"),
            "role": payload.get("role"),
            "session_id": payload.get("session_id"),
        }
        return await call_next(request)
