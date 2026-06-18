"""Create + freeze action proposals — Phase 6 (ADR 0025, doc 04).

A proposal's *substance* (target, action, parameters, evidence, …) is frozen by
a ``content_hash``: a human approves that hash, the gateway executes that hash,
and execution recomputes it to detect tampering.  Severity is *computed* here
(doc 04 §Approval authority), never chosen by the model.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wolf_server.audit.log import write_event
from wolf_server.gateway.models import ActionProposal, ProposalState

# Default proposal TTL — short, because active response is time-sensitive
# (doc 04 §Stale proposals).  Operator-tunable via 6.10 settings later.
DEFAULT_TTL_SECONDS = 900  # 15 minutes

# Active-response commands Wolf treats as high-severity (irreversible / broad).
# Block-an-IP style commands stay "low"; everything not listed defaults low.
# Severity is informational in v1 (B1 = every write needs approval regardless);
# the field exists so severity-tiered authority can switch on it later.
_HIGH_SEVERITY_COMMANDS = frozenset({"restart-wazuh", "isolate", "host-isolation"})


def compute_severity(action_class: str, action: str, parameters: dict[str, Any]) -> str:
    """Compute the proposal's severity from its action class + command.

    v1: active-response block-style commands are ``low``; a small set of
    broad/irreversible commands are ``high``.  A future crown-jewel tag on the
    target escalates one level (doc 04 axis 3) — not implemented here.
    """
    if action_class == "active_response" and action in _HIGH_SEVERITY_COMMANDS:
        return "high"
    return "low"


def compute_content_hash(
    *,
    organization_id: uuid.UUID,
    action_class: str,
    target: dict[str, Any],
    action: str,
    parameters: dict[str, Any],
    evidence: dict[str, Any],
    expected_effect: str,
    rollback_plan: str | None,
    severity: str,
    requested_by: uuid.UUID,
) -> str:
    """SHA-256 over the proposal's immutable substance (canonical JSON)."""
    payload = {
        "organization_id": str(organization_id),
        "action_class": action_class,
        "target": target,
        "action": action,
        "parameters": parameters,
        "evidence": evidence,
        "expected_effect": expected_effect,
        "rollback_plan": rollback_plan,
        "severity": severity,
        "requested_by": str(requested_by),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def recompute_content_hash(proposal: ActionProposal) -> str:
    """Recompute the hash from a persisted proposal's immutable columns."""
    return compute_content_hash(
        organization_id=proposal.organization_id,
        action_class=proposal.action_class,
        target=proposal.target,
        action=proposal.action,
        parameters=proposal.parameters,
        evidence=proposal.evidence,
        expected_effect=proposal.expected_effect,
        rollback_plan=proposal.rollback_plan,
        severity=proposal.severity,
        requested_by=proposal.requested_by,
    )


def is_expired(proposal: ActionProposal, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    expires = proposal.expires_at
    # Persisted naive datetimes (SQLite) → treat as UTC for the comparison.
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return now >= expires


async def create_proposal(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    requested_by: uuid.UUID,
    action_class: str,
    target: dict[str, Any],
    action: str,
    rationale: str,
    expected_effect: str,
    evidence: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
    rollback_plan: str | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    session_id: str | None = None,
) -> ActionProposal:
    """Build, hash, persist (state=pending) + audit a proposal.

    Flushes but does not commit — the caller owns the transaction (matches the
    audit-write contract).  The validator (gateway.validator) must have passed
    BEFORE this is called: a persisted proposal is already in the queue.
    """
    parameters = parameters or {}
    evidence = evidence or {}
    severity = compute_severity(action_class, action, parameters)
    content_hash = compute_content_hash(
        organization_id=organization_id,
        action_class=action_class,
        target=target,
        action=action,
        parameters=parameters,
        evidence=evidence,
        expected_effect=expected_effect,
        rollback_plan=rollback_plan,
        severity=severity,
        requested_by=requested_by,
    )
    now = datetime.now(UTC)
    proposal = ActionProposal(
        organization_id=organization_id,
        action_class=action_class,
        target=target,
        action=action,
        parameters=parameters,
        rationale=rationale,
        evidence=evidence,
        expected_effect=expected_effect,
        rollback_plan=rollback_plan,
        severity=severity,
        requested_by=requested_by,
        content_hash=content_hash,
        state=ProposalState.pending,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
    db.add(proposal)
    await db.flush()

    await write_event(
        db,
        event_type="action.proposal.created",
        organization_id=organization_id,
        user_id=requested_by,
        session_id=session_id,
        event_data={
            "proposal_id": str(proposal.id),
            "action_class": action_class,
            "action": action,
            "severity": severity,
            "target": target,
            "content_hash": content_hash,
            "state": ProposalState.pending.value,
        },
    )
    return proposal
