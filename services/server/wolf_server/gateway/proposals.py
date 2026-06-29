"""Create + freeze action proposals — Phase 6 (ADR 0025, doc 04).

A proposal's *substance* (target, action, parameters, evidence, …) is frozen by
a ``content_hash``: a human approves that hash, the gateway executes that hash,
and execution recomputes it to detect tampering.  Severity is *computed* here
(doc 04 §Approval authority), never chosen by the model.
"""

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
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


class SeverityFn(Protocol):
    """A per-class severity function (registered in :data:`_SEVERITY`)."""

    def __call__(self, action: str, parameters: dict[str, Any]) -> str: ...


_SEVERITY: dict[str, SeverityFn] = {}


def register_severity(action_class: str, fn: SeverityFn) -> None:
    """Register the severity function for an action class (ADR 0029)."""
    _SEVERITY[action_class] = fn


def compute_severity(action_class: str, action: str, parameters: dict[str, Any]) -> str:
    """Compute the proposal's severity DYNAMICALLY from the action + its context.

    Two inputs combine (doc 04 §Approval authority): each class declares a **base
    impact** per action, and a higher-risk *target* escalates it one tier
    (context escalation).  Per-class (ADR 0029): each action class registers its
    own severity function; an unregistered class is ``low`` (conservative
    default — but a registered validator is required before it reaches here).

    Severity is informational in v1 (B1 = every write needs approval regardless)
    but it drives the queue's visual priority + the future severity-tiered
    authority, so it must be honest.
    """
    fn = _SEVERITY.get(action_class)
    if fn is None:
        return SEV_LOW
    return fn(action, parameters)


def _active_response_severity(action: str, parameters: dict[str, Any]) -> str:
    """Active-response base impact (block = high, disable = medium, restart =
    low — from the catalog) + privileged-account escalation (disable root/admin
    → high)."""
    cmd = get_ar_command(action)
    base = cmd.severity if cmd is not None else SEV_LOW
    if base == SEV_MEDIUM and _is_privileged_user(parameters.get("username")):
        return SEV_HIGH
    return base


register_severity("active_response", _active_response_severity)


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
    reverses_proposal_id: uuid.UUID | None = None,
) -> str:
    """SHA-256 over the proposal's immutable substance (canonical JSON).

    ``reverses_proposal_id`` is part of the substance for a reversal: the
    approver approves *which* block is being undone (ADR 0028).
    """
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
        "reverses_proposal_id": str(reverses_proposal_id) if reverses_proposal_id else None,
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
        reverses_proposal_id=proposal.reverses_proposal_id,
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
    reverses_proposal_id: uuid.UUID | None = None,
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
        reverses_proposal_id=reverses_proposal_id,
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
        reverses_proposal_id=reverses_proposal_id,
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
            "reverses_proposal_id": (
                str(reverses_proposal_id) if reverses_proposal_id else None
            ),
        },
    )
    return proposal


def stamp_auto_unblock_at(proposal: ActionProposal, *, now: datetime | None = None) -> None:
    """For a just-succeeded TIMED block, set ``auto_unblock_at`` so the reversal
    sweep (6-d.3) picks it up (ADR 0028).  No-op when the block carries no
    ``block_duration_seconds`` (an indefinite block has no automatic reversal).
    """
    params = proposal.parameters if isinstance(proposal.parameters, dict) else {}
    duration = params.get("block_duration_seconds")
    if not isinstance(duration, int) or duration <= 0:
        return
    base = proposal.executed_at or now or datetime.now(UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    proposal.auto_unblock_at = base + timedelta(seconds=duration)


async def find_active_action(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    action_class: str,
    matcher: Callable[[ActionProposal], bool],
) -> ActionProposal | None:
    """The most-recent *succeeded, not-yet-reversed* action of ``action_class``
    on this org's ledger that satisfies ``matcher`` (ADR 0029 provenance recall,
    generalized from :func:`find_active_block`).

    "Not yet reversed" = a forward action (``reverses_proposal_id IS NULL``) that
    hasn't been reversed (``reversal_proposal_id IS NULL``) and is still
    ``succeeded`` (a ``rolled_back`` one was already undone).  This is Wolf's
    record of what it *dispatched/applied*, not necessarily live host state.
    """
    stmt = (
        select(ActionProposal)
        .where(
            ActionProposal.organization_id == organization_id,
            ActionProposal.action_class == action_class,
            ActionProposal.state == ProposalState.succeeded,
            ActionProposal.reverses_proposal_id.is_(None),  # a forward action, not a reversal
            ActionProposal.reversal_proposal_id.is_(None),  # not already reversed
        )
        .order_by(ActionProposal.executed_at.desc(), ActionProposal.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    for row in rows:
        if matcher(row):
            return row
    return None


async def find_active_block(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    action_class: str,
    agent_id: str,
    srcip: str | None = None,
    username: str | None = None,
) -> ActionProposal | None:
    """The most-recent *succeeded, not-yet-reversed* active-response block for the
    given agent + target (ADR 0028) — a thin srcip/username matcher over
    :func:`find_active_action`."""

    def _matches(p: ActionProposal) -> bool:
        params = p.parameters if isinstance(p.parameters, dict) else {}
        if str(p.target.get("agent_id", "")) != agent_id:
            return False
        if srcip is not None and params.get("srcip") == srcip:
            return True
        return username is not None and params.get("username") == username

    return await find_active_action(
        db, organization_id=organization_id, action_class=action_class, matcher=_matches
    )


async def list_active_blocks(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    action_class: str = "active_response",
    limit: int = 200,
) -> list[ActionProposal]:
    """This org's *succeeded, not-yet-reversed* blocks — Wolf's dispatch ledger
    (ADR 0028).  Newest first.  NOT live host state (that arrives with wolf-pack)."""
    stmt = (
        select(ActionProposal)
        .where(
            ActionProposal.organization_id == organization_id,
            ActionProposal.action_class == action_class,
            ActionProposal.state == ProposalState.succeeded,
            ActionProposal.reverses_proposal_id.is_(None),
            ActionProposal.reversal_proposal_id.is_(None),
        )
        .order_by(ActionProposal.executed_at.desc(), ActionProposal.created_at.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def create_reversal_proposal(
    db: AsyncSession,
    block: ActionProposal,
    *,
    requested_by: uuid.UUID,
    action: str,
    parameters: dict[str, Any],
    rationale: str,
    expected_effect: str,
    evidence: dict[str, Any] | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    session_id: str | None = None,
) -> ActionProposal:
    """Create a reversal proposal linked to ``block`` and stamp the block so it
    is not reversed twice / swept again (ADR 0028).

    The reversal inherits the block's org + action_class + target; the caller
    supplies the resolved command (same as the block's, delete-inverse) and the
    parameters (with ``reversal=True``).
    """
    reversal = await create_proposal(
        db,
        organization_id=block.organization_id,
        requested_by=requested_by,
        action_class=block.action_class,
        target=block.target,
        action=action,
        parameters=parameters,
        rationale=rationale,
        expected_effect=expected_effect,
        evidence=evidence,
        reverses_proposal_id=block.id,
        ttl_seconds=ttl_seconds,
        session_id=session_id,
    )
    # Stamp the block so the sweep won't re-fire and the GUI shows it as
    # reversal-authorised. The block stays ``succeeded`` (still in effect) until
    # wolf-pack confirms the physical removal and flips it to ``rolled_back``.
    block.reversal_proposal_id = reversal.id
    await db.flush()
    return reversal
