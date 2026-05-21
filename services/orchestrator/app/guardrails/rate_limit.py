"""Per-tenant rate limiter — token bucket, in-process.

In-process is fine for single-replica deployments and the development inner
loop.  Multi-replica deployments must swap this for a Redis-backed limiter
(same interface, different implementation).

The bucket is keyed by tenant_id; one tenant cannot consume another's share.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass

from app.guardrails.limits import GuardrailViolation


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class TenantRateLimiter:
    """Token-bucket rate limiter, one bucket per tenant.

    Default: 60 tool calls per minute per tenant, bursting up to 60.
    """

    def __init__(self, *, rate_per_minute: float = 60.0, burst: int = 60) -> None:
        self._rate_per_sec = rate_per_minute / 60.0
        self._burst = float(burst)
        self._buckets: dict[uuid.UUID, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def take(self, tenant_id: uuid.UUID, *, cost: float = 1.0) -> None:
        """Consume `cost` tokens from the tenant's bucket.

        Raises GuardrailViolation if not enough tokens are available.
        """
        async with self._lock:
            now = time.monotonic()
            bucket = self._buckets.get(tenant_id)
            if bucket is None:
                bucket = _Bucket(tokens=self._burst, last_refill=now)
                self._buckets[tenant_id] = bucket
            else:
                elapsed = now - bucket.last_refill
                bucket.tokens = min(self._burst, bucket.tokens + elapsed * self._rate_per_sec)
                bucket.last_refill = now

            if bucket.tokens < cost:
                raise GuardrailViolation(
                    f"Rate limit exceeded for tenant {tenant_id} "
                    f"({self._rate_per_sec * 60:.0f}/min)"
                )
            bucket.tokens -= cost


# Module-level singleton — swap to Redis-backed in multi-replica deployments.
default_rate_limiter = TenantRateLimiter()
