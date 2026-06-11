"""Session blacklist — Phase 6.5-g, ADR 0018 §"Session cookie blacklisting".

Server-side revocation for session cookies.  Deleting a cookie client-side
never invalidates the JWT itself — anyone holding a copied token could keep
using it until expiry.  The blacklist closes that gap: AuthMiddleware
consults it on every authenticated request, and revoked sessions get 401
immediately, in every tab.

Two revocation shapes (both TTL-bounded, entries expire with the tokens
they outlive):

  revoke_session(session_id)  — one session.  Trigger: explicit logout.
  revoke_user(user_id)        — EVERY outstanding session for a user, via a
      timestamp watermark: any token *issued before* the revocation moment
      is dead, tokens issued by a later re-login are fine.  Triggers:
      Superuser password reset, force-revoke (compromised account).

Backend selection follows the Slice 4.3 cache precedent (protocol-first,
operator-approved 2026-06-11 for this slice too): the in-memory store is
the default and is correct for Wolf's single-process deployment shape
(uvicorn, one worker — see __main__.py); setting REDIS_URL in the env
activates the Redis-backed store for multi-worker installs or
revocation-survives-restart requirements.  The redis *client* library is a
regular dependency; the redis *server* is the operator's own, never a
.deb dependency.

Known, accepted limit of the in-memory default: a wolf-server restart
forgets revocations.  The exposure window is bounded by the access-token
expiry (60 min default).  Operators who need restart-proof revocation set
REDIS_URL.

Note for the future refresh-token endpoint (none exists today): it MUST
check `is_revoked` with the refresh token's iat/session_id too, and
revoke_user TTLs must then cover the refresh lifetime (7 days), not just
the access lifetime.
"""

import time
from collections.abc import Callable
from typing import Protocol

import structlog

from wolf_server.config import get_settings

logger = structlog.get_logger(__name__)

_SESSION_KEY_PREFIX = "wolf:session-blacklist:session:"
_USER_KEY_PREFIX = "wolf:session-blacklist:user:"


class SessionBlacklist(Protocol):
    """The blacklist API.  All times are seconds; iat is epoch seconds."""

    async def revoke_session(self, session_id: str, *, ttl_seconds: int) -> None:
        """Revoke one session.  ttl_seconds should cover the token's
        remaining lifetime; the entry self-expires after that."""
        ...

    async def revoke_user(self, user_id: str, *, ttl_seconds: int) -> None:
        """Revoke every session issued to `user_id` up to this moment
        (watermark).  Sessions created by a later re-login are unaffected."""
        ...

    async def is_revoked(self, session_id: str, user_id: str, issued_at: float) -> bool:
        """True if this session is revoked — either directly or because the
        token was issued at/before the user's revocation watermark."""
        ...


class InMemorySessionBlacklist:
    """Default backend — correct for Wolf's single-process deployment.

    Wall-clock (`time.time`) drives the watermark comparison because token
    iat claims are epoch seconds; monotonic time drives entry expiry so
    host clock adjustments cannot resurrect or immortalise entries.  Both
    clocks are injectable for deterministic tests.
    """

    def __init__(
        self,
        *,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._wall = wall_clock
        self._mono = monotonic_clock
        # session_id -> entry expiry (monotonic seconds)
        self._sessions: dict[str, float] = {}
        # user_id -> (watermark epoch seconds, entry expiry monotonic seconds)
        self._user_watermarks: dict[str, tuple[float, float]] = {}

    def _purge(self) -> None:
        now = self._mono()
        self._sessions = {sid: exp for sid, exp in self._sessions.items() if exp > now}
        self._user_watermarks = {
            uid: (mark, exp) for uid, (mark, exp) in self._user_watermarks.items() if exp > now
        }

    async def revoke_session(self, session_id: str, *, ttl_seconds: int) -> None:
        self._purge()
        self._sessions[session_id] = self._mono() + ttl_seconds

    async def revoke_user(self, user_id: str, *, ttl_seconds: int) -> None:
        self._purge()
        self._user_watermarks[user_id] = (self._wall(), self._mono() + ttl_seconds)

    async def is_revoked(self, session_id: str, user_id: str, issued_at: float) -> bool:
        self._purge()
        if session_id in self._sessions:
            return True
        watermark = self._user_watermarks.get(user_id)
        return watermark is not None and issued_at <= watermark[0]


class RedisSessionBlacklist:
    """Redis backend — activated by REDIS_URL; shared across workers and
    surviving wolf-server restarts.  Key TTLs are managed by Redis (EX)."""

    def __init__(self, redis_url: str) -> None:
        # Local import keeps redis out of the import graph for the default
        # in-memory path (faster startup, no client needed unless used).
        import redis.asyncio as redis_asyncio  # noqa: PLC0415

        self._redis = redis_asyncio.Redis.from_url(redis_url, decode_responses=True)

    async def revoke_session(self, session_id: str, *, ttl_seconds: int) -> None:
        await self._redis.set(f"{_SESSION_KEY_PREFIX}{session_id}", "1", ex=ttl_seconds)

    async def revoke_user(self, user_id: str, *, ttl_seconds: int) -> None:
        await self._redis.set(f"{_USER_KEY_PREFIX}{user_id}", str(time.time()), ex=ttl_seconds)

    async def is_revoked(self, session_id: str, user_id: str, issued_at: float) -> bool:
        # One round trip for both checks.
        session_hit, watermark_raw = await self._redis.mget(
            f"{_SESSION_KEY_PREFIX}{session_id}", f"{_USER_KEY_PREFIX}{user_id}"
        )
        if session_hit is not None:
            return True
        return watermark_raw is not None and issued_at <= float(watermark_raw)


# ── Factory ──────────────────────────────────────────────────────────────────

_instance: SessionBlacklist | None = None


def get_session_blacklist() -> SessionBlacklist:
    """Process-wide singleton, backend chosen by settings.redis_url.

    Middleware and the trigger sites (logout, password reset, force-revoke)
    must all share ONE instance — a revocation written by a handler has to
    be visible to the middleware on the next request.
    """
    global _instance  # noqa: PLW0603 — deliberate process-wide singleton
    if _instance is None:
        redis_url = get_settings().redis_url
        if redis_url:
            _instance = RedisSessionBlacklist(redis_url)
            logger.info("session_blacklist_backend", backend="redis")
        else:
            _instance = InMemorySessionBlacklist()
            logger.info("session_blacklist_backend", backend="in-memory")
    return _instance


def reset_session_blacklist() -> None:
    """Drop the singleton — tests only (forces re-selection from settings)."""
    global _instance  # noqa: PLW0603
    _instance = None
