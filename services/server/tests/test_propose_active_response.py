"""propose_active_response tool — Phase 6 (ADR 0025).

The tool must: reject an invented command (validator hard gate), refuse when the
credential's RBAC doesn't permit the action (capability pre-flight), and on
success persist a *pending* proposal — never execute.
"""

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_server.gateway.models import ActionProposal, ProposalState
from wolf_server.guardrails.limits import DEFAULT_LIMITS
from wolf_server.organization.context import OrganizationContext
from wolf_server.tools.base import ToolExecContext
from wolf_server.tools.propose_active_response import (
    ProposeActiveResponseInput,
    ProposeActiveResponseTool,
)
from wolf_server.wazuh.capabilities import ACTION_ACTIVE_RESPONSE


class _StubServerApi:
    """Returns a fixed effective-policies payload for the capability pre-flight."""

    def __init__(self, policies: dict[str, dict[str, str]]) -> None:
        self._policies = policies

    async def get(self, _path: str, *, params: Any = None) -> Any:
        return {"data": self._policies}


def _ctx(seed: dict[str, Any]) -> OrganizationContext:
    return OrganizationContext(
        organization_id=seed["organization_id"],
        organization_slug=seed["organization_slug"],
        user_id=seed["user_id"],
        user_email=seed["user_email"],
        role=seed["role"],
        session_id="sess-1",
    )


def _exec_ctx(
    db: AsyncSession, ctx: OrganizationContext, policies: dict[str, dict[str, str]]
) -> ToolExecContext:
    return ToolExecContext(
        organization=ctx,
        limits=DEFAULT_LIMITS,
        opensearch=None,
        server_api=_StubServerApi(policies),
        db=db,
    )


_ALLOW = {ACTION_ACTIVE_RESPONSE: {"agent:id:*": "allow"}}


@pytest.mark.asyncio
async def test_propose_rejects_invented_command(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW),
        ProposeActiveResponseInput(agent_id="001", command="rm-rf", rationale="x"),
    )
    assert out.permitted is False
    assert out.state == "rejected"
    assert out.proposal_id == ""


@pytest.mark.asyncio
async def test_propose_refused_when_credential_lacks_capability(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, {}),  # empty policies → fail closed
        ProposeActiveResponseInput(agent_id="001", command="firewall-drop", rationale="x"),
    )
    assert out.permitted is False
    assert "not authorized" in out.summary.lower() or "not authorized" in out.detail.lower()


@pytest.mark.asyncio
async def test_propose_success_persists_pending_proposal(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW),
        ProposeActiveResponseInput(
            agent_id="001", command="firewall-drop", rationale="brute force", alert_ids=["a1"]
        ),
    )
    assert out.permitted is True
    assert out.state == "pending"
    assert out.proposal_id

    proposal = (
        await db.execute(
            select(ActionProposal).where(
                ActionProposal.organization_id == ctx.organization_id
            )
        )
    ).scalar_one()
    assert proposal.state == ProposalState.pending
    assert proposal.action == "firewall-drop"
    assert proposal.requested_by == ctx.user_id
