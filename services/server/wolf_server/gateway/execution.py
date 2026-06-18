"""Proposal execution — freshness → bounded write → verification (doc 04).

``approved → executing → succeeded|failed``, with the doc-04 safety steps:

  - **content-hash integrity** — recompute the hash from the persisted immutable
    columns; a mismatch aborts (tamper / drift between approval and execution).
  - **freshness re-check** — re-query the evidence + target state; if the world
    moved, the permission is void → the proposal is expired, not executed
    (doc 04 §Stale proposals).
  - **bounded write** — the actual state change, via the capability-checked
    action client (never the model, never the read client).
  - **verification read** — record the *actual* end-state, not the API's
    optimistic return value (doc 04 §Partial execution); failure never retries
    blind.

The three side-effecting steps (freshness / perform / verify) are injected
callables so the gateway logic is decoupled from live Wazuh and fully testable;
the API layer composes them from the per-org Wazuh clients.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from wolf_common.errors import WolfError

from wolf_server.audit.log import write_event
from wolf_server.gateway.models import ActionProposal, ProposalState
from wolf_server.gateway.proposals import recompute_content_hash
from wolf_server.gateway.state_machine import assert_transition

# (proposal) -> (is_fresh, human-readable detail)
FreshnessCheck = Callable[[ActionProposal], Awaitable[tuple[bool, str]]]
# (proposal) -> raw result of the bounded write; raises on transport failure
PerformAction = Callable[[ActionProposal], Awaitable[dict[str, Any]]]
# (proposal, perform_result) -> (succeeded, verification-read detail)
VerifyAction = Callable[[ActionProposal, dict[str, Any]], Awaitable[tuple[bool, dict[str, Any]]]]


class ContentHashMismatchError(WolfError):
    """The proposal's substance changed between approval and execution."""

    http_status = 409
    error_code = "content_hash_mismatch"


class ProposalStaleError(WolfError):
    """The freshness re-check failed — the world moved; the action is void."""

    http_status = 409
    error_code = "proposal_stale"


async def execute_proposal(
    db: AsyncSession,
    proposal: ActionProposal,
    *,
    freshness: FreshnessCheck,
    perform: PerformAction,
    verify: VerifyAction,
    executor_user_id: uuid.UUID,
    session_id: str | None = None,
) -> ActionProposal:
    """Execute an *approved* proposal end-to-end, recording the actual outcome."""
    # Integrity: the approved hash must still describe the action.
    if recompute_content_hash(proposal) != proposal.content_hash:
        raise ContentHashMismatchError(
            "Proposal content hash mismatch — refusing to execute a changed action."
        )

    # Freshness: if the world moved, the permission is void.
    fresh, fresh_detail = await freshness(proposal)
    if not fresh:
        assert_transition(ProposalState(proposal.state), ProposalState.expired)
        proposal.state = ProposalState.expired
        proposal.result = {"freshness": fresh_detail}
        await _audit(db, proposal, "action.proposal.stale", executor_user_id, session_id)
        raise ProposalStaleError(
            f"Freshness re-check failed: {fresh_detail}. Re-propose the action."
        )

    # approved → executing.
    assert_transition(ProposalState(proposal.state), ProposalState.executing)
    proposal.state = ProposalState.executing
    await _audit(db, proposal, "action.proposal.executing", executor_user_id, session_id)

    # The bounded write.  A transport/permission failure → failed end-state, no retry.
    try:
        perform_result = await perform(proposal)
    except Exception as exc:  # noqa: BLE001 — record the failure, never retry blind
        proposal.state = ProposalState.failed
        proposal.executed_at = datetime.now(UTC)
        proposal.result = {"error": str(exc)[:500]}
        await _audit(db, proposal, "action.proposal.failed", executor_user_id, session_id)
        return proposal

    # Verification read — the authoritative end-state.
    ok, verify_detail = await verify(proposal, perform_result)
    target_state = ProposalState.succeeded if ok else ProposalState.failed
    assert_transition(ProposalState(proposal.state), target_state)
    proposal.state = target_state
    proposal.executed_at = datetime.now(UTC)
    proposal.result = verify_detail
    await _audit(
        db,
        proposal,
        f"action.proposal.{target_state.value}",
        executor_user_id,
        session_id,
    )
    return proposal


async def _audit(
    db: AsyncSession,
    proposal: ActionProposal,
    event_type: str,
    user_id: uuid.UUID,
    session_id: str | None,
) -> None:
    await write_event(
        db,
        event_type=event_type,
        organization_id=proposal.organization_id,
        user_id=user_id,
        session_id=session_id,
        event_data={
            "proposal_id": str(proposal.id),
            "action_class": proposal.action_class,
            "action": proposal.action,
            "content_hash": proposal.content_hash,
            "state": proposal.state,
            "result": proposal.result,
        },
    )
