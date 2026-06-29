"""Reversal execution — the wolf-pack-bound `perform`/`verify`/`freshness` for an
undo proposal (slice 6-d, ADR 0028).

A reversal proposal (``reverses_proposal_id`` set) cannot physically run through
the Wazuh Server API: execd always rewrites a fresh invocation to ``add`` — there
is no API path to dispatch a ``delete``. Under **Option A** the reversal is
*authorised + recorded* now; the physical host removal is performed by **wolf-pack
(Phase 12)**, which fills the single ``perform`` seam below.

So `reversal_perform` touches nothing on the host — it records what the undo WILL
do (the catalog's ``reverses_via``) and that it is wolf-pack-bound; `reversal_verify`
lands the proposal ``succeeded`` meaning *the reversal was authorised + recorded*
(NOT host-applied), with an explicit ``reversal_state`` the GUI surfaces. The
linked block is left ``succeeded`` (still in effect) until wolf-pack confirms the
removal and flips it to ``rolled_back`` — Wolf never claims an IP is unblocked
when it is not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from wolf_server.audit.log import write_event
from wolf_server.gateway.models import ActionProposal, ProposalState
from wolf_server.gateway.state_machine import assert_transition
from wolf_server.wazuh.active_response import get_ar_command

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

# Markers the reversal records in its result (GUI / audit) — whether the host
# change is still pending (AR, wolf-pack-bound) or has actually completed
# (API-executable classes: agent_action group moves, rule/config snapshot-restore).
REVERSAL_STATE_PENDING = "authorized_pending_wolf_pack"
REVERSAL_STATE_COMPLETED = "completed"

_WOLF_PACK_NOTE = (
    "Reversal authorised and recorded. The physical removal runs on the host via "
    "wolf-pack (Phase 12); the Wazuh Server API cannot dispatch an active-response "
    "'delete'. The block stays in effect until wolf-pack confirms removal."
)


def is_reversal(proposal: ActionProposal) -> bool:
    """True iff this proposal undoes another (an unblock / re-enable)."""
    return proposal.reverses_proposal_id is not None


async def reversal_perform(proposal: ActionProposal) -> dict[str, Any]:
    """The reversal 'bounded write' — records the undo plan, touches no host.

    This is the seam wolf-pack later fills with the real ``delete`` on the host.
    """
    cmd = get_ar_command(proposal.action)
    return {
        "deferred_to": "wolf-pack",
        "reverses_via": cmd.reverses_via if cmd is not None else "",
        "note": _WOLF_PACK_NOTE,
    }


async def reversal_verify(
    proposal: ActionProposal, perform_result: dict[str, Any]
) -> tuple[bool, dict[str, Any]]:
    """The reversal 'verification' — the authorisation + record succeeded; the
    host change is pending wolf-pack.  Honest: ``dispatched`` is False (no host
    action), but the proposal lifecycle completes (the directive is recorded)."""
    detail = dict(perform_result)
    detail["dispatched"] = False
    detail["reversal_state"] = REVERSAL_STATE_PENDING
    return True, detail


async def reversal_freshness(
    db: AsyncSession, proposal: ActionProposal
) -> tuple[bool, str]:
    """Reversal freshness — the block being undone must still be a live block on
    Wolf's ledger (``succeeded`` and not already physically reversed).  This is
    Wolf's own record, honest that live host state is unknown until wolf-pack."""
    block_id = proposal.reverses_proposal_id
    if block_id is None:  # pragma: no cover — only called for reversals
        return True, "No linked block (defensive)."
    block = await db.get(ActionProposal, block_id)
    if block is None:
        return False, "The original block record is no longer present."
    if block.state == ProposalState.rolled_back:
        return False, "The block has already been reversed."
    return True, f"Block {block_id} still on record (state={block.state})."


async def complete_api_reversal(
    db: AsyncSession,
    reversal: ActionProposal,
    *,
    executor_user_id: uuid.UUID,
    session_id: str | None = None,
) -> bool:
    """For an **API-executable** reversal that SUCCEEDED (the undo really ran on
    Wazuh — agent_action / rule_tuning / config_change), flip the original action
    ``succeeded → rolled_back`` + audit it.  No-op for AR (wolf-pack-bound: its
    result is ``REVERSAL_STATE_PENDING``, so the original stays ``succeeded``
    until wolf-pack confirms).  Idempotent: only acts on a still-``succeeded``
    original.  Returns whether it flipped one."""
    result = reversal.result if isinstance(reversal.result, dict) else {}
    if (
        reversal.state != ProposalState.succeeded
        or result.get("reversal_state") != REVERSAL_STATE_COMPLETED
    ):
        return False
    block_id = reversal.reverses_proposal_id
    if block_id is None:
        return False
    original = await db.get(ActionProposal, block_id)
    if original is None or original.state != ProposalState.succeeded:
        return False
    assert_transition(ProposalState(original.state), ProposalState.rolled_back)
    original.state = ProposalState.rolled_back
    await write_event(
        db,
        event_type="action.proposal.rolled_back",
        organization_id=original.organization_id,
        user_id=executor_user_id,
        session_id=session_id,
        event_data={
            "proposal_id": str(original.id),
            "reversed_by": str(reversal.id),
            "action_class": original.action_class,
            "action": original.action,
            "content_hash": original.content_hash,
        },
    )
    return True
