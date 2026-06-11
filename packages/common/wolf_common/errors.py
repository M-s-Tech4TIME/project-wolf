"""Error taxonomy for Wolf services.

All Wolf errors inherit from WolfError so service boundaries can catch the
right breadth. Security-relevant errors (organization mismatch, permission denied)
are distinct types to ensure they are logged and handled deliberately.
"""

from http import HTTPStatus


class WolfError(Exception):
    """Base class for all Wolf application errors."""

    http_status: int = HTTPStatus.INTERNAL_SERVER_ERROR
    error_code: str = "wolf_error"


# ─── Tenancy ──────────────────────────────────────────────────────────────────


class OrganizationContextError(WolfError):
    """Raised when a organization context is missing or cannot be established."""

    http_status = HTTPStatus.BAD_REQUEST
    error_code = "organization_context_error"


class OrganizationMismatchError(WolfError):
    """Raised when returned data does not match the request's organization context.

    This is a security event — it is always logged as a security incident.
    The request must fail closed; no data is returned.
    """

    http_status = HTTPStatus.INTERNAL_SERVER_ERROR
    error_code = "organization_mismatch"


class OrganizationNotFoundError(WolfError):
    """Raised when the requested organization does not exist or is inactive."""

    http_status = HTTPStatus.NOT_FOUND
    error_code = "organization_not_found"


# ─── Authentication / authorisation ───────────────────────────────────────────


class AuthenticationError(WolfError):
    """Invalid credentials."""

    http_status = HTTPStatus.UNAUTHORIZED
    error_code = "authentication_error"


class AuthorizationError(WolfError):
    """Authenticated user lacks permission for this action."""

    http_status = HTTPStatus.FORBIDDEN
    error_code = "authorization_error"


class SessionExpiredError(WolfError):
    """The session token has expired or been revoked."""

    http_status = HTTPStatus.UNAUTHORIZED
    error_code = "session_expired"


# ─── Tools / capabilities ─────────────────────────────────────────────────────


class ToolNotFoundError(WolfError):
    """The requested tool does not exist in the registry."""

    http_status = HTTPStatus.NOT_FOUND
    error_code = "tool_not_found"


class ToolCapabilityError(WolfError):
    """The requested tool is not permitted for the current capability tier."""

    http_status = HTTPStatus.FORBIDDEN
    error_code = "tool_capability_error"


class ToolSchemaError(WolfError):
    """Tool input or output does not match the strict schema."""

    http_status = HTTPStatus.UNPROCESSABLE_ENTITY
    error_code = "tool_schema_error"


# ─── Secrets ──────────────────────────────────────────────────────────────────


class SecretNotFoundError(WolfError):
    """The requested secret key does not exist in the backend."""

    http_status = HTTPStatus.NOT_FOUND
    error_code = "secret_not_found"


class SecretBackendError(WolfError):
    """The secrets backend is unavailable or returned an unexpected error."""

    http_status = HTTPStatus.SERVICE_UNAVAILABLE
    error_code = "secret_backend_error"
