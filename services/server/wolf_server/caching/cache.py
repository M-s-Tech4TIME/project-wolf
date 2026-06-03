"""TenantScopedCache + InMemoryTenantCache.

Implementation note: the type system enforces the tenant prefix. Callers
cannot construct a storage key without supplying `tenant_id`. The
backend (in-memory dict here, Redis later) sees the composed key only.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import uuid


class UnprefixedKeyError(ValueError):
    """Raised when a cache key composition is attempted without tenant_id.

    Defence-in-depth — the public API takes tenant_id as a positional
    argument so the only way to hit this exception is to call an
    internal method directly. Worth raising loudly when that happens.
    """


def _compose_storage_key(tenant_id: uuid.UUID, namespace: str, key: str) -> str:
    """Build the actual storage-layer key from (tenant_id, namespace, key).

    The colon-delimited format `t:<uuid>:<ns>:<key>` is chosen so that:
      - The tenant prefix is at the front, satisfying doc 05's
        "mandatory prefix" requirement.
      - A Redis-backed implementation can scan keys by tenant prefix
        for tenant deletion / audit.
      - Eyeball-scanning cache logs makes a tenant breach obvious.
    """
    if tenant_id is None:
        raise UnprefixedKeyError(
            "Cache key construction requires a tenant_id (doc 05 §Caching "
            "across tenants). Use TenantScopedCache.set(tenant_id, ...) "
            "instead of constructing a raw key."
        )
    if not namespace or ":" in namespace:
        raise ValueError(
            f"namespace must be a non-empty string without colons; got {namespace!r}"
        )
    return f"t:{tenant_id}:{namespace}:{key}"


class TenantScopedCache(Protocol):
    """The public API. Every operation requires a tenant_id."""

    async def get(
        self, tenant_id: uuid.UUID, namespace: str, key: str
    ) -> object | None:
        """Return the cached value or None on miss / TTL expiry."""

    async def set(
        self,
        tenant_id: uuid.UUID,
        namespace: str,
        key: str,
        value: object,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        """Store a value, optionally with a TTL."""

    async def invalidate(
        self, tenant_id: uuid.UUID, namespace: str, key: str
    ) -> None:
        """Drop a single entry. No-op on miss."""

    async def invalidate_tenant(self, tenant_id: uuid.UUID) -> int:
        """Drop every entry for one tenant. Returns count removed.

        Used at tenant offboarding and for blast-radius limiting on
        suspected leaks.
        """


class InMemoryTenantCache:
    """Process-local in-memory implementation. No eviction beyond TTL.

    Fine for single-process dev / small deployments. For multi-process
    wolf-server the operator would swap in a Redis-backed implementation
    honoring the same protocol; this module's design deliberately avoids
    assuming a backend.
    """

    def __init__(self) -> None:
        # Storage: composed-key → (value, expiry_unix_or_None)
        self._store: dict[str, tuple[object, float | None]] = {}

    async def get(
        self, tenant_id: uuid.UUID, namespace: str, key: str
    ) -> object | None:
        storage_key = _compose_storage_key(tenant_id, namespace, key)
        entry = self._store.get(storage_key)
        if entry is None:
            return None
        value, expiry = entry
        if expiry is not None and time.monotonic() >= expiry:
            # Lazy eviction on read; saves a background sweep thread
            # at the cost of a per-tenant "garbage" footprint between
            # the entry expiring and someone asking for it.
            del self._store[storage_key]
            return None
        return value

    async def set(
        self,
        tenant_id: uuid.UUID,
        namespace: str,
        key: str,
        value: object,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        storage_key = _compose_storage_key(tenant_id, namespace, key)
        expiry = time.monotonic() + ttl_seconds if ttl_seconds is not None else None
        self._store[storage_key] = (value, expiry)

    async def invalidate(
        self, tenant_id: uuid.UUID, namespace: str, key: str
    ) -> None:
        storage_key = _compose_storage_key(tenant_id, namespace, key)
        self._store.pop(storage_key, None)

    async def invalidate_tenant(self, tenant_id: uuid.UUID) -> int:
        prefix = f"t:{tenant_id}:"
        # snapshot keys before iterating to avoid mutation-during-iteration.
        to_drop = [k for k in self._store if k.startswith(prefix)]
        for k in to_drop:
            del self._store[k]
        return len(to_drop)

    # Test / introspection helpers — not part of the protocol so a
    # Redis backend doesn't need to implement them.

    def _size(self) -> int:
        """Total entries across all tenants. Tests use this; production code shouldn't."""
        return len(self._store)

    def _storage_keys(self) -> list[str]:
        """Snapshot of currently-stored composed keys. Tests-only."""
        return list(self._store.keys())
