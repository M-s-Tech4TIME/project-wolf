"""propose_rule_tuning tool — Phase 6-e.3 (ADR 0029).

The tool validates the rule id + op + level, capability-pre-flights ``rules:update``
(Superuser-scoped, manager-global), and on success queues a *pending* proposal —
never executes. ``restore_rules`` for a rule with an active prior Wolf change is
the UNDO: linked + recalled + the original stamped.
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
from wolf_server.tools.propose_rule_tuning import (
    ProposeRuleTuningInput,
    ProposeRuleTuningTool,
)
from wolf_server.wazuh.capabilities import ACTION_UPDATE_RULES

_ALLOW_RULES = {ACTION_UPDATE_RULES: {"rule:file:*": "allow"}}


class _StubServerApi:
    """Serves the only read the propose path makes — effective policies."""

    def __init__(self, policies: dict[str, dict[str, str]]) -> None:
        self._policies = policies

    async def get(self, path: str, *, params: Any = None) -> Any:
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


async def _seed_rule_tuning(
    db: AsyncSession, ctx: OrganizationContext, *, rule_id: str, rationale: str
) -> ActionProposal:
    now = datetime.now(UTC)
    p = ActionProposal(
        organization_id=ctx.organization_id,
        action_class="rule_tuning",
        target={"rule_id": rule_id},
        action="disable_rule",
        parameters={"level": 0},
        rationale=rationale,
        evidence={},
        expected_effect="silence noisy rule",
        rollback_plan=None,
        severity="high",
        requested_by=ctx.user_id,
        content_hash="0" * 64,
        state=ProposalState.succeeded,
        prior_state={"filename": "local_rules.xml", "content": "<x/>", "sha256": "h"},
        executed_at=now,
        created_at=now,
        expires_at=now,
    )
    db.add(p)
    await db.flush()
    return p


@pytest.mark.asyncio
async def test_propose_disable_rule_forward(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    out = await ProposeRuleTuningTool().run(
        _exec_ctx(db, ctx, _ALLOW_RULES),
        ProposeRuleTuningInput(rule_id="100001", operation="disable_rule", rationale="too noisy"),
    )
    assert out.permitted is True and out.state == "pending"
    proposal = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.organization_id == ctx.organization_id)
        )
    ).scalar_one()
    assert proposal.action_class == "rule_tuning"
    assert proposal.action == "disable_rule"
    assert proposal.target.get("rule_id") == "100001"
    assert proposal.parameters.get("level") == 0
    assert proposal.severity == "high"
    assert proposal.reverses_proposal_id is None


@pytest.mark.asyncio
async def test_propose_adjust_level_forward(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    out = await ProposeRuleTuningTool().run(
        _exec_ctx(db, ctx, _ALLOW_RULES),
        ProposeRuleTuningInput(rule_id="100001", operation="adjust_level", level=3),
    )
    assert out.permitted is True
    proposal = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.organization_id == ctx.organization_id)
        )
    ).scalar_one()
    assert proposal.action == "adjust_level"
    assert proposal.parameters.get("level") == 3


@pytest.mark.asyncio
async def test_propose_refused_without_rules_update(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    out = await ProposeRuleTuningTool().run(
        _exec_ctx(db, ctx, {}),  # per-org-style cred without rules:update
        ProposeRuleTuningInput(rule_id="100001", operation="disable_rule"),
    )
    assert out.permitted is False
    assert "rules:update" in out.detail.lower() or "superuser" in out.detail.lower()


@pytest.mark.asyncio
async def test_propose_adjust_without_level_refused(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    out = await ProposeRuleTuningTool().run(
        _exec_ctx(db, ctx, _ALLOW_RULES),
        ProposeRuleTuningInput(rule_id="100001", operation="adjust_level"),  # no level
    )
    assert out.permitted is False
    assert "level" in out.detail.lower()


@pytest.mark.asyncio
async def test_propose_invalid_rule_id_refused(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    out = await ProposeRuleTuningTool().run(
        _exec_ctx(db, ctx, _ALLOW_RULES),
        ProposeRuleTuningInput(rule_id="not-a-rule", operation="disable_rule"),
    )
    assert out.permitted is False
    assert "rule id" in out.detail.lower()


@pytest.mark.asyncio
async def test_propose_restore_links_and_recalls(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    disabled = await _seed_rule_tuning(
        db, ctx, rule_id="100001", rationale="too noisy: scanner false positives"
    )
    out = await ProposeRuleTuningTool().run(
        _exec_ctx(db, ctx, _ALLOW_RULES),
        ProposeRuleTuningInput(rule_id="100001", operation="restore_rules"),
    )
    assert out.permitted is True
    assert "too noisy: scanner false positives" in out.summary  # recalled reason
    reversal = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.reverses_proposal_id == disabled.id)
        )
    ).scalar_one()
    assert reversal.action == "restore_rules"
    await db.refresh(disabled)
    assert disabled.reversal_proposal_id == reversal.id  # the prior change is stamped


@pytest.mark.asyncio
async def test_propose_restore_with_nothing_to_undo_refused(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    out = await ProposeRuleTuningTool().run(
        _exec_ctx(db, ctx, _ALLOW_RULES),
        ProposeRuleTuningInput(rule_id="100001", operation="restore_rules"),
    )
    assert out.permitted is False
    assert "nothing to undo" in out.summary.lower() or "no active" in out.detail.lower()
