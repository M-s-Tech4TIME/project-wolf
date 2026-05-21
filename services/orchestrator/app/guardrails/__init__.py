"""Resource guardrails — orthogonal to capability tiers.

Capability tiers (read/propose/execute) govern WHAT KIND of effect a tool has;
guardrails govern BLAST RADIUS.  Even a read tool can exhaust the indexer or
pull megabytes of PII into context — these limits apply to every tool call
before it executes.

See doc 03 §Resource guardrails for the rationale.
"""

from app.guardrails.limits import GuardrailViolation, ResourceLimits, enforce_limits
from app.guardrails.rate_limit import TenantRateLimiter

__all__ = [
    "GuardrailViolation",
    "ResourceLimits",
    "TenantRateLimiter",
    "enforce_limits",
]
