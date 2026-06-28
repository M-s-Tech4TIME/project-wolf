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

from wolf_server.gateway.models import ActionProposal, ProposalState
from wolf_server.wazuh.active_response import get_ar_command

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Marker the reversal records in its result so the GUI / audit are unambiguous
# that the *host* change has not happened yet (it is wolf-pack-bound).
REVERSAL_STATE_PENDING = "authorized_pending_wolf_pack"

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
