"""Timed auto-reversal scheduler — slice 6-d.3 (ADR 0028).

When a block is created with a duration ("block X for 1h"), its execution stamps
``auto_unblock_at``. This in-process sweep (launched from ``main.py`` ``lifespan``)
periodically reverses any block whose window has expired — **system-initiated**
and **pre-consented by the timed-block's approval** (the approver who authorised
"block for 1h" authorised the expiry reversal as the second half of that one
time-boxed action), so it fires without a second human approval but is fully
recorded + audited + surfaced in ``/actions``.

Like every reversal (Option A), the auto-reversal does NOT touch the host: it
records the directive (``reversal_perform``) and the block stays in effect until
wolf-pack performs the physical removal. The Wazuh API cannot dispatch a
``delete``, and Wazuh's own ``<timeout>`` is config-side/fixed — so the
*arbitrary-duration* timer is Wolf-owned, here.

Idempotency / single-instance safety: a due block is claimed under
``SELECT … FOR UPDATE SKIP LOCKED`` (a no-op on SQLite) and re-checked
``reversal_proposal_id IS NULL`` before reversing; creating the reversal stamps
the block, so neither a re-run nor a concurrent manual unblock double-fires.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select

from wolf_server.audit.log import write_event
from wolf_server.database import db_session
from wolf_server.gateway.execution import execute_proposal
from wolf_server.gateway.models import ActionProposal, ProposalState
from wolf_server.gateway.proposals import create_reversal_proposal
from wolf_server.gateway.reversal import (
    reversal_freshness,
    reversal_perform,
    reversal_verify,
)
from wolf_server.wazuh.active_response import (
    INTENT_ENABLE_USER,
    INTENT_UNBLOCK_IP,
    get_ar_command,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

# Sentinel actor for system-initiated (pre-consented) auto-reversals. Not a real
# user; the audit trail attributes the action to Wolf's scheduler, distinct from
# any human approver (separation of duties does not apply to a pre-consented
# auto-reversal — the timed-block approval IS the consent).
WOLF_SYSTEM_ACTOR = uuid.UUID("00000000-0000-0000-0000-000000000000")


async def _due_block_ids(db: AsyncSession, *, now: datetime, limit: int) -> list[uuid.UUID]:
    """Ids of timed blocks whose auto-reversal is due and not yet authorised."""
    stmt = (
        select(ActionProposal.id)
        .where(
            ActionProposal.state == ProposalState.succeeded,
            ActionProposal.reversal_proposal_id.is_(None),
            ActionProposal.auto_unblock_at.is_not(None),
            ActionProposal.auto_unblock_at <= now,
        )
        .order_by(ActionProposal.auto_unblock_at)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _claim_block(
    db: AsyncSession, block_id: uuid.UUID, *, now: datetime
) -> ActionProposal | None:
    """Re-load + lock one candidate, returning it only if still due + unreversed
    (handles a race with a manual unblock; idempotent on re-run)."""
    stmt = (
        select(ActionProposal)
        .where(
            ActionProposal.id == block_id,
            ActionProposal.state == ProposalState.succeeded,
            ActionProposal.reversal_proposal_id.is_(None),
            ActionProposal.auto_unblock_at.is_not(None),
            ActionProposal.auto_unblock_at <= now,
        )
        .with_for_update(skip_locked=True)  # real lock on Postgres; no-op on SQLite
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _auto_reverse(db: AsyncSession, block: ActionProposal, *, now: datetime) -> None:
    """Create + (pre-consented) approve + execute the auto-reversal for ``block``."""
    params = block.parameters if isinstance(block.parameters, dict) else {}
    srcip = params.get("srcip")
    username = params.get("username")
    intent = INTENT_UNBLOCK_IP if srcip else INTENT_ENABLE_USER
    rev_params: dict[str, object] = {
        "intent": intent,
        "method_source": "auto_reversal",
        "reversal": True,
        "auto": True,
    }
    if isinstance(srcip, str):
        rev_params["srcip"] = srcip
    if isinstance(username, str):
        rev_params["username"] = username
    if isinstance(params.get("agent_os"), str):
        rev_params["agent_os"] = params["agent_os"]

    expired = block.auto_unblock_at or now
    expired_s = expired.strftime("%Y-%m-%d %H:%M UTC")
    cmd = get_ar_command(block.action)
    reverses_via = cmd.reverses_via if cmd is not None else ""
    target = srcip or username or ""
    rationale = (
        f"Automatic reversal: the timed block expired at {expired_s}. "
        f"Original block: {block.rationale}"
    )
    expected_effect = (
        f"Auto-reverse '{block.action}' on agent {block.target.get('agent_id', '')} "
        f"({target}) — the timed block expired. {reverses_via} Physical removal is "
        "performed by wolf-pack (Phase 12)."
    )
    orig_alerts = (
        block.evidence.get("alert_ids", []) if isinstance(block.evidence, dict) else []
    )
    evidence = {
        "reverses_proposal_id": str(block.id),
        "original_rationale": block.rationale,
        "original_alert_ids": orig_alerts,
        "auto_reversal": True,
        "expired_at": expired.isoformat(),
    }
    reversal = await create_reversal_proposal(
        db,
        block,
        requested_by=WOLF_SYSTEM_ACTOR,
        action=block.action,
        parameters=rev_params,
        rationale=rationale,
        expected_effect=expected_effect,
        evidence=evidence,
    )
    # Pre-consented approval: the timed-block's approval authorised this expiry
    # reversal (no second human approval; no separation-of-duties check).
    reversal.state = ProposalState.approved
    reversal.approved_by = WOLF_SYSTEM_ACTOR
    reversal.approved_at = now
    await write_event(
        db,
        event_type="action.proposal.auto_reversal.approved",
        organization_id=reversal.organization_id,
        user_id=WOLF_SYSTEM_ACTOR,
        session_id=None,
        event_data={
            "proposal_id": str(reversal.id),
            "reverses_proposal_id": str(block.id),
            "content_hash": reversal.content_hash,
            "expired_at": expired.isoformat(),
        },
    )

    async def _fresh(p: ActionProposal) -> tuple[bool, str]:
        return await reversal_freshness(db, p)

    await execute_proposal(
        db,
        reversal,
        freshness=_fresh,
        perform=reversal_perform,
        verify=reversal_verify,
        executor_user_id=WOLF_SYSTEM_ACTOR,
    )


async def sweep_due_reversals(*, now: datetime | None = None, limit: int = 100) -> int:
    """One sweep: auto-reverse every timed block whose window has expired.

    Each block is handled in its own transaction so one failure can't poison the
    rest; returns the number of blocks successfully auto-reversed.
    """
    now = now or datetime.now(UTC)
    async with db_session() as db:
        candidates = await _due_block_ids(db, now=now, limit=limit)
    processed = 0
    for block_id in candidates:
        async with db_session() as db:
            block = await _claim_block(db, block_id, now=now)
            if block is None:
                continue  # already reversed (race) or no longer due — idempotent
            try:
                await _auto_reverse(db, block, now=now)
                await db.commit()
                processed += 1
            except Exception:  # noqa: BLE001 — log + continue; next tick retries
                await db.rollback()
                logger.exception("auto_reversal_failed", proposal_id=str(block_id))
    if processed:
        logger.info("auto_reversals_swept", count=processed)
    return processed


async def run_auto_reversal_scheduler(
    *, interval_seconds: int, stop_event: asyncio.Event
) -> None:
    """The background loop: sweep, then wait ``interval_seconds`` (or until stop).

    A sweep failure is logged and never kills the loop. Cancellation /
    ``stop_event`` ends it cleanly at the next boundary.
    """
    logger.info("auto_reversal_scheduler_started", interval_s=interval_seconds)
    try:
        while not stop_event.is_set():
            try:
                await sweep_due_reversals()
            except Exception:  # noqa: BLE001 — the loop must survive any sweep error
                logger.exception("auto_reversal_sweep_error")
            # Wait the interval, but wake immediately if asked to stop. A timeout
            # just means the interval elapsed → sweep again.
            with suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
    except asyncio.CancelledError:  # pragma: no cover — shutdown path
        logger.info("auto_reversal_scheduler_cancelled")
        raise
    finally:
        logger.info("auto_reversal_scheduler_stopped")
