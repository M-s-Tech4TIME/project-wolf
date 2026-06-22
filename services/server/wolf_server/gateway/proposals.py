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
from wolf_server.wazuh.active_response import (
    SEV_HIGH,
    SEV_LOW,
    SEV_MEDIUM,
    get_ar_command,
)

# Default proposal TTL — short, because active response is time-sensitive
# (doc 04 §Stale proposals).  Operator-tunable via 6.10 settings later.
DEFAULT_TTL_SECONDS = 900  # 15 minutes

# Local accounts Wolf treats as privileged — disabling one is higher-impact than
# disabling an ordinary user, so it escalates the base severity one tier.
_PRIVILEGED_USERNAMES = frozenset({"root", "admin", "administrator", "sa"})


def _is_privileged_user(username: object) -> bool:
    if not isinstance(username, str):
        return False
    name = username.strip().lower()
    return name in _PRIVILEGED_USERNAMES or name.startswith("admin")


def compute_severity(action_class: str, action: str, parameters: dict[str, Any]) -> str:
    """Compute the proposal's severity DYNAMICALLY from the action + its context.

    Two inputs combine (doc 04 §Approval authority):

    1. **Base impact** — each active-response command declares its base severity
       in the catalog (block an IP = ``high`` — network enforcement with
       collateral risk; disable a user = ``medium``; restart the agent =
       ``low``).  The catalog is the single source of truth, so a new command
       sets its own severity instead of this function guessing.
    2. **Context escalation** — a higher-risk *target* raises the base one tier:
       disabling a privileged account (root / admin) is treated as ``high``, not
       ``medium``.  Crown-jewel target tags and broad targets are further axes
       (doc 04 axis 3) — the hook lives here; v1 ships the privileged-account
       escalation.

    Severity is informational in v1 (B1 = every write needs approval regardless)
    but it drives the queue's visual priority and the future severity-tiered
    authority, so it must be honest.
    """
    if action_class != "active_response":
        return SEV_LOW
    cmd = get_ar_command(action)
    base = cmd.severity if cmd is not None else SEV_LOW
    if base == SEV_MEDIUM and _is_privileged_user(parameters.get("username")):
        return SEV_HIGH
    return base


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
