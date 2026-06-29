"""Action-proposal approval API — Phase 6 (ADR 0025, doc 04).

Org-scoped, capability-gated endpoints over the in-process approval gateway:

    GET    /api/v1/organization/action-proposals            list (default: pending)
    GET    /api/v1/organization/action-proposals/{id}       one proposal
    POST   /api/v1/organization/action-proposals/{id}/approve   approve + execute
    POST   /api/v1/organization/action-proposals/{id}/reject    decline

Listing/reading needs ``ACTION_PROPOSE`` (anyone who contributes to the queue);
deciding needs ``ACTION_APPROVE``.  Every row is forced-filtered by
``organization_id`` (the cross-organization isolation boundary).  Approval and
execution are one transaction: approve (separation of duties) → freshness
re-check → bounded write → verification read → audit, all committed together.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_common.errors import WolfError

from wolf_server.config import get_settings
from wolf_server.database import get_db
from wolf_server.gateway.approval import approve_proposal, reject_proposal
from wolf_server.gateway.execution import execute_proposal
from wolf_server.gateway.executors import ExecContext, get_executor
from wolf_server.gateway.models import ActionProposal, ProposalState
from wolf_server.gateway.proposals import stamp_auto_unblock_at
from wolf_server.gateway.reversal import is_reversal
from wolf_server.organization.context import OrganizationContext
from wolf_server.organization.rbac import Capability, require_capability
from wolf_server.secrets_factory import get_secrets_backend
from wolf_server.wazuh.capabilities import fetch_credential_capabilities
from wolf_server.wazuh.resolver import get_wazuh_connection
from wolf_server.wazuh.server_api import WazuhServerApiActionClient, WazuhServerApiClient

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/organization/action-proposals", tags=["action-proposals"])

_settings = get_settings()


class ProposalOut(BaseModel):
    id: str
    action_class: str
    action: str
    target: dict[str, Any]
    parameters: dict[str, Any]
    severity: str
    state: str
    rationale: str
    expected_effect: str
    evidence: dict[str, Any]
    rollback_plan: str | None
    requested_by: str
    approved_by: str | None
    approved_at: datetime | None
    executed_at: datetime | None
    result: dict[str, Any] | None
    created_at: datetime
    expires_at: datetime
    # Reversal linkage (slice 6-d, ADR 0028) — for the /actions surface.
    reverses_proposal_id: str | None
    reversal_proposal_id: str | None
    auto_unblock_at: datetime | None

    @classmethod
    def from_row(cls, p: ActionProposal) -> "ProposalOut":
        return cls(
            id=str(p.id),
            action_class=p.action_class,
            action=p.action,
            target=p.target,
            parameters=p.parameters,
            severity=p.severity,
            state=p.state,
            rationale=p.rationale,
            expected_effect=p.expected_effect,
            evidence=p.evidence,
            rollback_plan=p.rollback_plan,
            requested_by=str(p.requested_by),
            approved_by=str(p.approved_by) if p.approved_by else None,
            approved_at=p.approved_at,
            executed_at=p.executed_at,
            result=p.result,
            created_at=p.created_at,
            expires_at=p.expires_at,
            reverses_proposal_id=(
                str(p.reverses_proposal_id) if p.reverses_proposal_id else None
            ),
            reversal_proposal_id=(
                str(p.reversal_proposal_id) if p.reversal_proposal_id else None
            ),
            auto_unblock_at=p.auto_unblock_at,
        )


class RejectRequest(BaseModel):
    reason: str = Field(default="", max_length=1000)


async def _load_proposal(
    db: AsyncSession, ctx: OrganizationContext, proposal_id: uuid.UUID
) -> ActionProposal:
    """Load one proposal, forced-filtered to the caller's organization."""
    stmt = select(ActionProposal).where(
        ActionProposal.id == proposal_id,
        ActionProposal.organization_id == ctx.organization_id,
    )
    proposal = (await db.execute(stmt)).scalar_one_or_none()
    if proposal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposal not found")
    return proposal


_RECENT_LIMIT = 200


@router.get("", response_model=list[ProposalOut])
async def list_proposals(
    ctx: Annotated[OrganizationContext, Depends(require_capability(Capability.ACTION_PROPOSE))],
    db: Annotated[AsyncSession, Depends(get_db)],
    state: Annotated[str, Query()] = ProposalState.pending.value,
) -> list[ProposalOut]:
    """List this org's proposals, newest first.

    ``state`` filters to one lifecycle state (default ``pending`` — the
    actionable approval queue).  ``state=all`` returns recent proposals across
    *every* state (the activity history that lets a reviewer see what was
    executed / failed / rejected), capped at the most recent ``_RECENT_LIMIT``.
    Always forced-filtered to the caller's organization.
    """
    stmt = select(ActionProposal).where(
        ActionProposal.organization_id == ctx.organization_id
    )
    if state != "all":
        stmt = stmt.where(ActionProposal.state == state)
    stmt = stmt.order_by(ActionProposal.created_at.desc()).limit(_RECENT_LIMIT)
    rows = (await db.execute(stmt)).scalars().all()
    return [ProposalOut.from_row(p) for p in rows]


@router.get("/{proposal_id}", response_model=ProposalOut)
async def get_proposal(
    proposal_id: uuid.UUID,
    ctx: Annotated[OrganizationContext, Depends(require_capability(Capability.ACTION_PROPOSE))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProposalOut:
    return ProposalOut.from_row(await _load_proposal(db, ctx, proposal_id))


@router.post("/{proposal_id}/reject", response_model=ProposalOut)
async def reject(
    proposal_id: uuid.UUID,
    body: RejectRequest,
    ctx: Annotated[OrganizationContext, Depends(require_capability(Capability.ACTION_APPROVE))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProposalOut:
    proposal = await _load_proposal(db, ctx, proposal_id)
    try:
        await reject_proposal(
            db,
            proposal,
            approver_user_id=ctx.user_id,
            approver_role=ctx.role,
            reason=body.reason,
            session_id=ctx.session_id,
        )
    except WolfError as exc:
        await db.commit()
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    await db.commit()
    return ProposalOut.from_row(proposal)


@router.post("/{proposal_id}/approve", response_model=ProposalOut)
async def approve(
    proposal_id: uuid.UUID,
    ctx: Annotated[OrganizationContext, Depends(require_capability(Capability.ACTION_APPROVE))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProposalOut:
    """Approve (separation of duties) then execute the proposal end-to-end."""
    proposal = await _load_proposal(db, ctx, proposal_id)

    # 1. Approve — capability + separation of duties + TTL + state edge.
    try:
        await approve_proposal(
            db,
            proposal,
            approver_user_id=ctx.user_id,
            approver_role=ctx.role,
            session_id=ctx.session_id,
        )
    except WolfError as exc:
        await db.commit()  # persist any audit / expiry the approval path recorded
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc

    # 2. Execute via the per-class executor (ADR 0029). The per-org Wazuh clients
    #    are opened uniformly — lazy auth means an executor that doesn't call them
    #    costs nothing (e.g. AR's wolf-pack-bound reversal). The executor supplies
    #    the forward vs reverse (freshness, perform, verify); execute_proposal (the
    #    engine) is class-agnostic.
    secrets = get_secrets_backend(_settings)
    connection = await get_wazuh_connection(ctx, db, secrets)
    try:
        async with (
            WazuhServerApiClient(connection) as read_api,
            WazuhServerApiActionClient(connection) as action_api,
        ):
            capabilities = await fetch_credential_capabilities(read_api)
            exec_ctx = ExecContext(
                read_api=read_api, action_api=action_api, capabilities=capabilities, db=db
            )
            executor = get_executor(proposal.action_class)
            freshness, perform, verify = (
                executor.build_reverse(proposal, exec_ctx)
                if is_reversal(proposal)
                else executor.build_forward(proposal, exec_ctx)
            )
            await execute_proposal(
                db,
                proposal,
                freshness=freshness,
                perform=perform,
                verify=verify,
                executor_user_id=ctx.user_id,
                session_id=ctx.session_id,
            )
    except WolfError as exc:
        await db.commit()  # persist the stale/mismatch audit + state change
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc

    # A succeeded TIMED forward action arms its automatic reversal (ADR 0028 sweep).
    if proposal.state == ProposalState.succeeded and not is_reversal(proposal):
        stamp_auto_unblock_at(proposal)

    await db.commit()
    return ProposalOut.from_row(proposal)
