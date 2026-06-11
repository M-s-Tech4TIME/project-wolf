"""Local-account authentication helpers.

Passwords are hashed with bcrypt.  Never store or log plaintext passwords.
JWT tokens are signed with HS256 and the SECRET_KEY from settings.

Token design:
  - access token  — short-lived (60 min default), carries user_id + organization_id + role
  - refresh token — long-lived (7 days), used to get a new access token

The organization_id in the token is the organization the user *selected* at login.
If a user belongs to multiple organizations they log in again to switch.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt
from wolf_common.errors import AuthenticationError, SessionExpiredError

from wolf_server.config import get_settings

_settings = get_settings()


# ── Password helpers ─────────────────────────────────────────────────────────


def hash_password(plaintext: str) -> str:
    """Return a bcrypt hash of `plaintext`."""
    return bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt()).decode()


def verify_password(plaintext: str, hashed: str) -> bool:
    """Return True if `plaintext` matches the stored `hashed` bcrypt digest."""
    return bcrypt.checkpw(plaintext.encode(), hashed.encode())


# ── JWT helpers ──────────────────────────────────────────────────────────────


def _make_token(data: dict[str, Any], expires_delta: timedelta) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(UTC) + expires_delta
    payload["iat"] = datetime.now(UTC)
    # `jose` ships no PEP-561 stubs (see root pyproject's mypy overrides),
    # so `jwt.encode` is typed as `Any`. Cast at the boundary so the `Any`
    # does not leak into call sites.
    encoded: str = jwt.encode(payload, _settings.secret_key, algorithm=_settings.jwt_algorithm)
    return encoded


def create_access_token(
    user_id: uuid.UUID,
    organization_id: uuid.UUID | None,
    role: str,
    session_id: str,
) -> str:
    # organization_id is None for the install-level Superuser (zero org
    # memberships by default — ADR 0018). Org-scoped dependencies treat
    # a None org claim as "no org context" and reject accordingly.
    return _make_token(
        {
            "sub": str(user_id),
            "organization_id": str(organization_id) if organization_id is not None else None,
            "role": role,
            "session_id": session_id,
            "token_type": "access",
        },
        timedelta(minutes=_settings.access_token_expire_minutes),
    )


def create_refresh_token(user_id: uuid.UUID, session_id: str) -> str:
    return _make_token(
        {
            "sub": str(user_id),
            "session_id": session_id,
            "token_type": "refresh",
        },
        timedelta(days=_settings.refresh_token_expire_days),
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate an access token.

    Raises AuthenticationError on invalid signature or expiry.
    Raises SessionExpiredError specifically when the token has expired.
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token, _settings.secret_key, algorithms=[_settings.jwt_algorithm]
        )
        if payload.get("token_type") != "access":
            raise AuthenticationError("Not an access token")
        return payload
    except JWTError as exc:
        if "expired" in str(exc).lower():
            raise SessionExpiredError("Access token has expired") from exc
        raise AuthenticationError("Invalid access token") from exc
