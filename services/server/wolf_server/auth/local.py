"""Local-account authentication helpers.

Passwords are hashed with bcrypt.  Never store or log plaintext passwords.
JWT tokens are signed with HS256 and the SECRET_KEY from settings.

Token design:
  - access token  — short-lived (60 min default), carries user_id + session_id ONLY
  - refresh token — long-lived (7 days), used to get a new access token

The access token is AUTHENTICATION only (ADR 0018 Round 3): it never
carries an organization or role. The active organization arrives per
request in the X-Organization-Id header and the membership binding is
validated on every request — see organization/context.py.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
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
    return jwt.encode(payload, _settings.secret_key, algorithm=_settings.jwt_algorithm)


def create_access_token(
    user_id: uuid.UUID,
    session_id: str,
) -> str:
    return _make_token(
        {
            "sub": str(user_id),
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
    except ExpiredSignatureError as exc:
        raise SessionExpiredError("Access token has expired") from exc
    except InvalidTokenError as exc:
        raise AuthenticationError("Invalid access token") from exc
    if payload.get("token_type") != "access":
        raise AuthenticationError("Not an access token")
    return payload
