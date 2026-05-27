"""Tests for the Phase 4 Slice 3 tenant-scoped cache.

The whole point of the wrapper is making it structurally impossible to
construct a cache key without a tenant prefix. These tests verify:

  1. Tenant A's set + Tenant B's get with the SAME (namespace, key) →
     B sees a miss (the doc 05 §Caching across tenants failure mode).
  2. TTL works (expired entries are not returned).
  3. invalidate_tenant() drops only that tenant's entries.
  4. The internal key composer raises if tenant_id is None.
"""

import asyncio
import uuid

import pytest
from app.caching import InMemoryTenantCache, UnprefixedKeyError
from app.caching.cache import _compose_storage_key

# ─── Cross-tenant isolation — THE load-bearing property ──────────────────


@pytest.mark.asyncio
async def test_tenant_a_set_does_not_leak_to_tenant_b() -> None:
    """Doc 05 §Caching across tenants: 'alerts_last_24h' for tenant A
    must never satisfy tenant B's request for the same key."""
    cache = InMemoryTenantCache()
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    await cache.set(tenant_a, "alerts", "last_24h", {"high": 12, "low": 8})

    # B asks for the same logical key — must MISS.
    b_value = await cache.get(tenant_b, "alerts", "last_24h")
    assert b_value is None, (
        "Cross-tenant cache leak: tenant B saw tenant A's cached value."
    )

    # A still hits its own entry.
    a_value = await cache.get(tenant_a, "alerts", "last_24h")
    assert a_value == {"high": 12, "low": 8}


@pytest.mark.asyncio
async def test_storage_keys_carry_tenant_prefix() -> None:
    """Belt-and-braces: every composed storage key starts with the
    tenant prefix. A Redis-backed implementation reading the raw keys
    should be able to scan-by-prefix for tenant operations."""
    cache = InMemoryTenantCache()
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    await cache.set(tenant_a, "agents", "linux-test-agent", "001")
    await cache.set(tenant_b, "agents", "linux-test-agent", "999")

    keys = cache._storage_keys()
    assert any(k.startswith(f"t:{tenant_a}:") for k in keys)
    assert any(k.startswith(f"t:{tenant_b}:") for k in keys)
    # And distinctly — not collapsing onto one shared key.
    assert len(set(keys)) == 2


# ─── Key construction — type-level guard ─────────────────────────────────


def test_compose_storage_key_rejects_none_tenant() -> None:
    """The internal composer raises if tenant_id is None.

    Public API takes tenant_id as a positional argument so this can only
    be reached by calling internals directly; raising is still the right
    behaviour for defence-in-depth."""
    with pytest.raises(UnprefixedKeyError, match="tenant_id"):
        _compose_storage_key(None, "ns", "k")  # type: ignore[arg-type]


def test_compose_storage_key_rejects_namespace_with_colon() -> None:
    """Namespace must not contain colons — that's the delimiter."""
    with pytest.raises(ValueError, match="without colons"):
        _compose_storage_key(uuid.uuid4(), "bad:namespace", "k")


def test_compose_storage_key_rejects_empty_namespace() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        _compose_storage_key(uuid.uuid4(), "", "k")


# ─── TTL ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ttl_expires_entry_on_next_read() -> None:
    cache = InMemoryTenantCache()
    tenant_a = uuid.uuid4()
    await cache.set(tenant_a, "ns", "k", "value", ttl_seconds=0.05)

    # Immediate get hits.
    assert await cache.get(tenant_a, "ns", "k") == "value"

    await asyncio.sleep(0.10)

    # After TTL elapses, get returns None and the entry is evicted.
    assert await cache.get(tenant_a, "ns", "k") is None
    # Eviction is lazy on read — but it DID happen. Size goes to 0.
    assert cache._size() == 0


@pytest.mark.asyncio
async def test_no_ttl_means_persistent_until_invalidated() -> None:
    cache = InMemoryTenantCache()
    tenant_a = uuid.uuid4()
    await cache.set(tenant_a, "ns", "k", "value")  # no ttl_seconds
    await asyncio.sleep(0.05)
    assert await cache.get(tenant_a, "ns", "k") == "value"


# ─── Invalidation ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalidate_drops_one_entry() -> None:
    cache = InMemoryTenantCache()
    tenant_a = uuid.uuid4()
    await cache.set(tenant_a, "ns", "k1", "v1")
    await cache.set(tenant_a, "ns", "k2", "v2")

    await cache.invalidate(tenant_a, "ns", "k1")
    assert await cache.get(tenant_a, "ns", "k1") is None
    assert await cache.get(tenant_a, "ns", "k2") == "v2"


@pytest.mark.asyncio
async def test_invalidate_tenant_drops_only_that_tenants_entries() -> None:
    """Tenant offboarding / suspected-leak blast-radius control."""
    cache = InMemoryTenantCache()
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await cache.set(tenant_a, "ns1", "k1", "a1")
    await cache.set(tenant_a, "ns2", "k2", "a2")
    await cache.set(tenant_b, "ns1", "k1", "b1")

    dropped = await cache.invalidate_tenant(tenant_a)
    assert dropped == 2
    assert cache._size() == 1
    # Tenant B's entry is untouched.
    assert await cache.get(tenant_b, "ns1", "k1") == "b1"


@pytest.mark.asyncio
async def test_invalidate_miss_is_safe_noop() -> None:
    cache = InMemoryTenantCache()
    tenant_a = uuid.uuid4()
    # No prior set; invalidate should not raise.
    await cache.invalidate(tenant_a, "ns", "missing")
