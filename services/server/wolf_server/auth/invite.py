"""Invite-token helpers — Phase 6.5-h (ADR 0018 item 9).

An Admin-created account starts ``unverified`` (organization/models.py) and
carries a single-use invite token.  The Admin copies the invite link
out-of-band; the user pastes it after logging in and the verify-invite
endpoint (api/auth.py) flips the account to ``verified``.

Token design:
  - The raw token is a 256-bit URL-safe random string.  It is shown ONCE
    at generation (in the create-member / regenerate responses) and never
    stored — only its SHA-256 hex digest lives on
    ``User.verification_token_hash``.
  - A plain SHA-256 is the right hash here (not bcrypt): the token is
    high-entropy random, so there is nothing to brute-force — a slow KDF
    would only add latency.  Comparison is constant-time all the same.
  - The token expires 7 days after generation
    (``User.verification_token_expires_at``) and is single-use (the hash +
    expiry are cleared the moment verification succeeds).
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

# 32 random bytes → ~43 url-safe chars.  Comfortably above brute-force range.
_TOKEN_BYTES = 32
INVITE_TOKEN_TTL = timedelta(days=7)


def hash_invite_token(raw_token: str) -> str:
    """Return the SHA-256 hex digest stored on the user row."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


def new_invite_token() -> tuple[str, str, datetime]:
    """Mint a fresh invite token.

    Returns ``(raw_token, token_hash, expires_at)``.  Persist the hash +
    expiry on the user; hand the raw token back to the caller exactly once.
    """
    raw_token = secrets.token_urlsafe(_TOKEN_BYTES)
    return raw_token, hash_invite_token(raw_token), datetime.now(UTC) + INVITE_TOKEN_TTL


def verify_invite_token(raw_token: str, stored_hash: str) -> bool:
    """Constant-time check that ``raw_token`` hashes to ``stored_hash``."""
    return secrets.compare_digest(hash_invite_token(raw_token), stored_hash)
