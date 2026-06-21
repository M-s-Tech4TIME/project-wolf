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
from wolf_server.gateway.models import ActionProposal, ProposalState
from wolf_server.organization.context import OrganizationContext
from wolf_server.organization.rbac import Capability, require_capability
from wolf_server.secrets_factory import get_secrets_backend
from wolf_server.wazuh.active_response import interpret_ar_result
from wolf_server.wazuh.capabilities import fetch_credential_capabilities, resolve_agent_groups
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

    # 2. Execute — freshness → bounded write → verification, via the per-org creds.
    secrets = get_secrets_backend(_settings)
    connection = await get_wazuh_connection(ctx, db, secrets)
    try:
        async with (
            WazuhServerApiClient(connection) as read_api,
            WazuhServerApiActionClient(connection) as action_api,
        ):
            capabilities = await fetch_credential_capabilities(read_api)

            async def _freshness(p: ActionProposal) -> tuple[bool, str]:
                agent_id = str(p.target.get("agent_id", ""))
                body = await read_api.get("/agents", params={"agents_list": agent_id})
                total = body.get("data", {}).get("total_affected_items", 0)
                if total and total >= 1:
                    return True, f"Agent {agent_id} still present."
                return False, f"Agent {agent_id} is no longer visible to the credential."

            async def _perform(p: ActionProposal) -> dict[str, Any]:
                agent_id = str(p.target.get("agent_id", ""))
                params = p.parameters if isinstance(p.parameters, dict) else {}
                raw_args = params.get("arguments", [])
                arguments = [str(a) for a in raw_args] if isinstance(raw_args, list) else []
                srcip = params.get("srcip")
                username = params.get("username")
                # Resolve the agent's groups fresh — the capability check expands
                # the grant over current group membership (Wazuh RBAC semantics).
                agent_groups = await resolve_agent_groups(read_api, agent_id)
                return await action_api.execute_active_response(
                    agent_id=agent_id,
                    command=p.action,
                    capabilities=capabilities,
                    agent_groups=agent_groups,
                    srcip=srcip if isinstance(srcip, str) else None,
                    username=username if isinstance(username, str) else None,
                    arguments=arguments,
                )

            async def _verify(
                p: ActionProposal, res: dict[str, Any]
            ) -> tuple[bool, dict[str, Any]]:
                # Wazuh returns HTTP 200 even on failure — interpret_ar_result
                # reads dispatch from the body (affected vs failed_items) and is
                # honest that "dispatched" != "applied on the host".
                return interpret_ar_result(res)

            await execute_proposal(
                db,
                proposal,
                freshness=_freshness,
                perform=_perform,
                verify=_verify,
                executor_user_id=ctx.user_id,
                session_id=ctx.session_id,
            )
    except WolfError as exc:
        await db.commit()  # persist the stale/mismatch audit + state change
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc

    await db.commit()
    return ProposalOut.from_row(proposal)
