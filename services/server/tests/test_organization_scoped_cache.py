"""Tests for the Phase 4 Slice 3 organization-scoped cache.

The whole point of the wrapper is making it structurally impossible to
construct a cache key without a organization prefix. These tests verify:

  1. Organization A's set + Organization B's get with the SAME (namespace, key) →
     B sees a miss (the doc 05 §Caching across organizations failure mode).
  2. TTL works (expired entries are not returned).
  3. invalidate_organization() drops only that organization's entries.
  4. The internal key composer raises if organization_id is None.
"""

import asyncio
import uuid

import pytest
from wolf_server.caching import InMemoryOrganizationCache, UnprefixedKeyError
from wolf_server.caching.cache import _compose_storage_key

# ─── Cross-organization isolation — THE load-bearing property ──────────────────


@pytest.mark.asyncio
async def test_organization_a_set_does_not_leak_to_organization_b() -> None:
    """Doc 05 §Caching across organizations: 'alerts_last_24h' for organization A
    must never satisfy organization B's request for the same key."""
    cache = InMemoryOrganizationCache()
    organization_a = uuid.uuid4()
    organization_b = uuid.uuid4()

    await cache.set(organization_a, "alerts", "last_24h", {"high": 12, "low": 8})

    # B asks for the same logical key — must MISS.
    b_value = await cache.get(organization_b, "alerts", "last_24h")
    assert b_value is None, (
        "Cross-organization cache leak: organization B saw organization A's cached value."
    )

    # A still hits its own entry.
    a_value = await cache.get(organization_a, "alerts", "last_24h")
    assert a_value == {"high": 12, "low": 8}


@pytest.mark.asyncio
async def test_storage_keys_carry_organization_prefix() -> None:
    """Belt-and-braces: every composed storage key starts with the
    organization prefix. A Redis-backed implementation reading the raw keys
    should be able to scan-by-prefix for organization operations."""
    cache = InMemoryOrganizationCache()
    organization_a = uuid.uuid4()
    organization_b = uuid.uuid4()

    await cache.set(organization_a, "agents", "linux-test-agent", "001")
    await cache.set(organization_b, "agents", "linux-test-agent", "999")

    keys = cache._storage_keys()
    assert any(k.startswith(f"t:{organization_a}:") for k in keys)
    assert any(k.startswith(f"t:{organization_b}:") for k in keys)
    # And distinctly — not collapsing onto one shared key.
    assert len(set(keys)) == 2


# ─── Key construction — type-level guard ─────────────────────────────────


def test_compose_storage_key_rejects_none_organization() -> None:
    """The internal composer raises if organization_id is None.

    Public API takes organization_id as a positional argument so this can only
    be reached by calling internals directly; raising is still the right
    behaviour for defence-in-depth."""
    with pytest.raises(UnprefixedKeyError, match="organization_id"):
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
    cache = InMemoryOrganizationCache()
    organization_a = uuid.uuid4()
    await cache.set(organization_a, "ns", "k", "value", ttl_seconds=0.05)

    # Immediate get hits.
    assert await cache.get(organization_a, "ns", "k") == "value"

    await asyncio.sleep(0.10)

    # After TTL elapses, get returns None and the entry is evicted.
    assert await cache.get(organization_a, "ns", "k") is None
    # Eviction is lazy on read — but it DID happen. Size goes to 0.
    assert cache._size() == 0


@pytest.mark.asyncio
async def test_no_ttl_means_persistent_until_invalidated() -> None:
    cache = InMemoryOrganizationCache()
    organization_a = uuid.uuid4()
    await cache.set(organization_a, "ns", "k", "value")  # no ttl_seconds
    await asyncio.sleep(0.05)
    assert await cache.get(organization_a, "ns", "k") == "value"


# ─── Invalidation ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalidate_drops_one_entry() -> None:
    cache = InMemoryOrganizationCache()
    organization_a = uuid.uuid4()
    await cache.set(organization_a, "ns", "k1", "v1")
    await cache.set(organization_a, "ns", "k2", "v2")

    await cache.invalidate(organization_a, "ns", "k1")
    assert await cache.get(organization_a, "ns", "k1") is None
    assert await cache.get(organization_a, "ns", "k2") == "v2"


@pytest.mark.asyncio
async def test_invalidate_organization_drops_only_that_organizations_entries() -> None:
    """Organization offboarding / suspected-leak blast-radius control."""
    cache = InMemoryOrganizationCache()
    organization_a = uuid.uuid4()
    organization_b = uuid.uuid4()
    await cache.set(organization_a, "ns1", "k1", "a1")
    await cache.set(organization_a, "ns2", "k2", "a2")
    await cache.set(organization_b, "ns1", "k1", "b1")

    dropped = await cache.invalidate_organization(organization_a)
    assert dropped == 2
    assert cache._size() == 1
    # Organization B's entry is untouched.
    assert await cache.get(organization_b, "ns1", "k1") == "b1"


@pytest.mark.asyncio
async def test_invalidate_miss_is_safe_noop() -> None:
    cache = InMemoryOrganizationCache()
    organization_a = uuid.uuid4()
    # No prior set; invalidate should not raise.
    await cache.invalidate(organization_a, "ns", "missing")
