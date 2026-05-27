"""Tenant-scoped cache (Phase 4 Slice 3 / doc 05 §Caching across tenants).

The whole purpose of this module is to make it structurally impossible to
construct a cache key without the tenant prefix. Any code path that wants
to cache MUST go through the TenantScopedCache wrapper, which composes
the storage key from (tenant_id, namespace, key) — there is no
escape-hatch `raw_set(key)` or `set_unscoped(key)`.

Default backend is an in-memory dict (fine for single-process dev /
small deployments). A future Redis backend would implement the same
protocol; the wrapper API stays the same.

Per doc 05's "Caching across tenants" failure mode: a key like
'alerts_last_24h' shared between Tenant A and Tenant B is a data leak.
The wrapper's only way to set a value is `cache.set(tenant_id, key,
value)` — the prefix is mandatory and enforced by the type system.
"""

from app.caching.cache import (
    InMemoryTenantCache,
    TenantScopedCache,
    UnprefixedKeyError,
)

__all__ = ["InMemoryTenantCache", "TenantScopedCache", "UnprefixedKeyError"]
