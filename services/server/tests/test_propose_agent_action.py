"""propose_agent_action tool — Phase 6-e.2 (ADR 0029).

agent_action group management: the tool resolves the agent, validates the
operation + group, capability-pre-flights ``agent:modify_group`` (Superuser-
scoped), and on success queues a *pending* proposal carrying the operation +
group — never executes. Proposing the opposite operation for an active prior
action is recognised as an UNDO (linked + recalled).
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_server.gateway.models import ActionProposal, ProposalState
from wolf_server.guardrails.limits import DEFAULT_LIMITS
from wolf_server.organization.context import OrganizationContext
from wolf_server.tools.base import ToolExecContext
from wolf_server.tools.propose_agent_action import (
    ProposeAgentActionInput,
    ProposeAgentActionTool,
)
from wolf_server.wazuh.capabilities import ACTION_MODIFY_GROUP


class _StubServerApi:
    """Serves the reads the propose path does: effective policies + the target
    agent's groups."""

    def __init__(
        self, policies: dict[str, dict[str, str]], agent_groups: list[str] | None = None
    ) -> None:
        self._policies = policies
        self._agent_groups = agent_groups if agent_groups is not None else ["default"]

    async def get(self, path: str, *, params: Any = None) -> Any:
        if path == "/agents":
            return {
                "data": {
                    "affected_items": [{"id": "001", "group": self._agent_groups}],
                    "total_affected_items": 1,
                }
            }
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
    db: AsyncSession,
    ctx: OrganizationContext,
    policies: dict[str, dict[str, str]],
    agent_groups: list[str] | None = None,
) -> ToolExecContext:
    return ToolExecContext(
        organization=ctx,
        limits=DEFAULT_LIMITS,
        opensearch=None,
        server_api=_StubServerApi(policies, agent_groups),
        db=db,
    )


_ALLOW = {ACTION_MODIFY_GROUP: {"agent:id:*": "allow"}}


async def _seed_agent_action(
    db: AsyncSession,
    ctx: OrganizationContext,
    *,
    operation: str,
    group: str,
    agent_id: str = "001",
    rationale: str = "quarantine: suspected compromise",
) -> ActionProposal:
    """Insert a (succeeded) agent_action so the undo path has a row to link/recall."""
    now = datetime.now(UTC)
    p = ActionProposal(
        organization_id=ctx.organization_id,
        action_class="agent_action",
        target={"agent_id": agent_id},
        action=operation,
        parameters={"group": group},
        rationale=rationale,
        evidence={},
        expected_effect="group change",
        rollback_plan=None,
        severity="medium",
        requested_by=ctx.user_id,
        content_hash="0" * 64,
        state=ProposalState.succeeded,
        executed_at=now,
        created_at=now,
        expires_at=now,
    )
    db.add(p)
    await db.flush()
    return p


@pytest.mark.asyncio
async def test_propose_assign_group_forward(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    out = await ProposeAgentActionTool().run(
        _exec_ctx(db, ctx, _ALLOW),
        ProposeAgentActionInput(
            agent_id="001", operation="assign_group", group="isolated", rationale="quarantine"
        ),
    )
    assert out.permitted is True and out.state == "pending"
    proposal = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.organization_id == ctx.organization_id)
        )
    ).scalar_one()
    assert proposal.action_class == "agent_action"
    assert proposal.action == "assign_group"
    assert proposal.parameters.get("group") == "isolated"
    assert proposal.severity == "medium"
    assert proposal.reverses_proposal_id is None  # a fresh forward action


@pytest.mark.asyncio
async def test_propose_refused_without_modify_group(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    out = await ProposeAgentActionTool().run(
        _exec_ctx(db, ctx, {}),  # per-org-style cred without modify_group
        ProposeAgentActionInput(agent_id="001", operation="assign_group", group="isolated"),
    )
    assert out.permitted is False
    assert "not authorized" in out.summary.lower() or "modify_group" in out.detail.lower()


@pytest.mark.asyncio
async def test_propose_invalid_group_refused(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    out = await ProposeAgentActionTool().run(
        _exec_ctx(db, ctx, _ALLOW),
        ProposeAgentActionInput(agent_id="001", operation="assign_group", group="bad/../name"),
    )
    assert out.permitted is False
    assert "group name" in out.detail.lower()


@pytest.mark.asyncio
async def test_propose_unknown_operation_refused(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    out = await ProposeAgentActionTool().run(
        _exec_ctx(db, ctx, _ALLOW),
        ProposeAgentActionInput(agent_id="001", operation="frobnicate", group="isolated"),
    )
    assert out.permitted is False
    assert "unknown agent action" in out.detail.lower()


@pytest.mark.asyncio
async def test_propose_remove_group_links_and_recalls_prior_assign(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    """Proposing remove_group for an agent+group with an active assign is the
    UNDO: it links + recalls why the agent was assigned (ADR 0029 reversal)."""
    ctx = _ctx(seed_organization_and_user)
    assign = await _seed_agent_action(
        db, ctx, operation="assign_group", group="isolated",
        rationale="quarantine: lateral movement detected",
    )
    out = await ProposeAgentActionTool().run(
        _exec_ctx(db, ctx, _ALLOW, agent_groups=["default", "isolated"]),
        ProposeAgentActionInput(agent_id="001", operation="remove_group", group="isolated"),
    )
    assert out.permitted is True
    assert "quarantine: lateral movement detected" in out.summary  # recalled reason
    reversal = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.reverses_proposal_id == assign.id)
        )
    ).scalar_one()
    assert reversal.action == "remove_group"
    await db.refresh(assign)
    assert assign.reversal_proposal_id == reversal.id  # the prior assign is stamped
