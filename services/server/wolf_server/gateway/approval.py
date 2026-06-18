"""Proposal approval / rejection — separation of duties + capability (doc 04).

A human with ``ACTION_APPROVE`` signs a *pending* proposal so the gateway may
execute it.  The structural guarantees enforced here (on top of the API-layer
role gate):

  - **Separation of duties** — the requester can never approve their own
    proposal (doc 04 §Collusion / self-approval).
  - **State gating** — only ``pending → approved`` / ``pending → rejected``;
    an expired proposal cannot be approved.

Every decision is an append-only audit event.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from wolf_common.errors import WolfError

from wolf_server.audit.log import write_event
from wolf_server.gateway.models import ActionProposal, ProposalState
from wolf_server.gateway.proposals import is_expired
from wolf_server.gateway.state_machine import assert_transition
from wolf_server.organization.rbac import Capability, role_has_capability


class SeparationOfDutiesError(WolfError):
    """The requester attempted to approve their own proposal."""

    http_status = 403
    error_code = "separation_of_duties"


class ApprovalAuthorityError(WolfError):
    """The approver's role lacks ACTION_APPROVE."""

    http_status = 403
    error_code = "approval_authority"


class ProposalExpiredError(WolfError):
    """The proposal's TTL elapsed before approval (doc 04 §Stale proposals)."""

    http_status = 409
    error_code = "proposal_expired"


async def approve_proposal(
    db: AsyncSession,
    proposal: ActionProposal,
    *,
    approver_user_id: uuid.UUID,
    approver_role: str,
    session_id: str | None = None,
) -> ActionProposal:
    """Sign a pending proposal (``pending → approved``).

    Enforces capability + separation of duties + TTL + the state edge, then
    records who approved which content hash.  Raises on any violation; the
    caller's transaction commits the approval together with the audit event.
    """
    if not role_has_capability(approver_role, Capability.ACTION_APPROVE):
        raise ApprovalAuthorityError(
            f"Role {approver_role!r} cannot approve action proposals."
        )
    if approver_user_id == proposal.requested_by:
        raise SeparationOfDutiesError(
            "The requester of a proposal cannot approve it — a different "
            "authorized user must approve."
        )
    if is_expired(proposal):
        # The TTL elapsed: move it to expired rather than approving a stale action.
        assert_transition(ProposalState(proposal.state), ProposalState.expired)
        proposal.state = ProposalState.expired
        await _audit(db, proposal, "action.proposal.expired", approver_user_id, session_id)
        raise ProposalExpiredError("This proposal has expired; re-propose the action.")

    assert_transition(ProposalState(proposal.state), ProposalState.approved)
    proposal.state = ProposalState.approved
    proposal.approved_by = approver_user_id
    proposal.approved_at = datetime.now(UTC)
    await _audit(db, proposal, "action.proposal.approved", approver_user_id, session_id)
    return proposal


async def reject_proposal(
    db: AsyncSession,
    proposal: ActionProposal,
    *,
    approver_user_id: uuid.UUID,
    approver_role: str,
    reason: str = "",
    session_id: str | None = None,
) -> ActionProposal:
    """Decline a pending proposal (``pending → rejected``).  Terminal.

    Requires ``ACTION_APPROVE`` (rejecting is an authority decision); unlike
    approval it has no separation-of-duties constraint — a requester may cancel
    their own proposal by rejecting it.
    """
    if not role_has_capability(approver_role, Capability.ACTION_APPROVE):
        raise ApprovalAuthorityError(
            f"Role {approver_role!r} cannot decide action proposals."
        )
    assert_transition(ProposalState(proposal.state), ProposalState.rejected)
    proposal.state = ProposalState.rejected
    await _audit(
        db,
        proposal,
        "action.proposal.rejected",
        approver_user_id,
        session_id,
        extra={"reason": reason},
    )
    return proposal


async def _audit(
    db: AsyncSession,
    proposal: ActionProposal,
    event_type: str,
    user_id: uuid.UUID,
    session_id: str | None,
    *,
    extra: dict[str, object] | None = None,
) -> None:
    data: dict[str, object] = {
        "proposal_id": str(proposal.id),
        "action_class": proposal.action_class,
        "action": proposal.action,
        "content_hash": proposal.content_hash,
        "state": proposal.state,
        "approver": str(user_id),
    }
    if extra:
        data.update(extra)
    await write_event(
        db,
        event_type=event_type,
        organization_id=proposal.organization_id,
        user_id=user_id,
        session_id=session_id,
        event_data=data,
    )
