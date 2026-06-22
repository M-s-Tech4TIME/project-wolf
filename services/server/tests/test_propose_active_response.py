"""propose_active_response tool — Phase 6 (ADR 0025), intent-driven (slice 6-c).

The model expresses a high-level INTENT (block_ip / disable_user / restart) and
Wolf resolves the agent's OS to deterministically pick the platform-correct
command. The tool must: reject an unknown intent, select the right command per
OS (block_ip → firewall-drop on Linux, netsh on Windows), refuse when the OS is
unknown / the intent is unsupported on the OS, refuse when the credential's RBAC
doesn't permit the action (capability pre-flight), and on success persist a
*pending* proposal carrying the resolved command — never execute.
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
    """Serves the reads the propose path does: effective policies
    (``/security/users/me/policies``), and the target agent's OS + groups
    (``/agents``). ``os_platform`` drives 6-c command selection; pass ``None``
    to simulate an agent whose OS can't be determined."""

    def __init__(
        self,
        policies: dict[str, dict[str, str]],
        agent_groups: list[str] | None = None,
        os_platform: str | None = "Ubuntu 22.04",
    ) -> None:
        self._policies = policies
        self._agent_groups = agent_groups or []
        self._os_platform = os_platform

    async def get(self, path: str, *, params: Any = None) -> Any:
        if path == "/agents":
            item: dict[str, Any] = {"id": "001", "group": self._agent_groups}
            if self._os_platform is not None:
                item["os"] = {"platform": self._os_platform}
            return {"data": {"affected_items": [item], "total_affected_items": 1}}
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
    os_platform: str | None = "Ubuntu 22.04",
) -> ToolExecContext:
    return ToolExecContext(
        organization=ctx,
        limits=DEFAULT_LIMITS,
        opensearch=None,
        server_api=_StubServerApi(policies, agent_groups, os_platform),
        db=db,
    )


_ALLOW = {ACTION_ACTIVE_RESPONSE: {"agent:id:*": "allow"}}
_ALLOW_BY_GROUP = {ACTION_ACTIVE_RESPONSE: {"agent:group:acme": "allow"}}


@pytest.mark.asyncio
async def test_propose_rejects_unknown_intent(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW),
        ProposeActiveResponseInput(agent_id="001", intent="quarantine", rationale="x"),
    )
    assert out.permitted is False
    assert out.state == "rejected"
    assert out.proposal_id == ""
    assert "intent" in out.detail.lower()


@pytest.mark.asyncio
async def test_propose_block_ip_selects_windows_command(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    """The headline 6-c behavior: a generic block_ip on a WINDOWS agent
    auto-selects `netsh` (not the Linux firewall-drop) with no model hint."""
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW, os_platform="Microsoft Windows Server 2019"),
        ProposeActiveResponseInput(
            agent_id="001", intent="block_ip", srcip="203.0.113.7", rationale="x"
        ),
    )
    assert out.permitted is True
    proposal = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.organization_id == ctx.organization_id)
        )
    ).scalar_one()
    assert proposal.action == "netsh"
    assert proposal.parameters.get("intent") == "block_ip"


@pytest.mark.asyncio
async def test_propose_refused_when_os_unknown_for_block_ip(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    """No OS signal → Wolf can't pick firewall-drop vs netsh → refused with
    guidance, never a guessed platform command."""
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW, os_platform=None),
        ProposeActiveResponseInput(
            agent_id="001", intent="block_ip", srcip="203.0.113.7", rationale="x"
        ),
    )
    assert out.permitted is False
    assert out.state == "rejected"
    assert "operating system" in out.detail.lower()


@pytest.mark.asyncio
async def test_propose_disable_user_unsupported_on_windows(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW, os_platform="Microsoft Windows Server 2019"),
        ProposeActiveResponseInput(
            agent_id="001", intent="disable_user", username="evil", rationale="x"
        ),
    )
    assert out.permitted is False
    assert "not supported on windows" in out.detail.lower()


@pytest.mark.asyncio
async def test_propose_restart_resolves_without_os(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    """restart is OS-agnostic — it resolves to restart-wazuh even when the OS
    can't be determined."""
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW, os_platform=None),
        ProposeActiveResponseInput(agent_id="001", intent="restart", rationale="x"),
    )
    assert out.permitted is True
    proposal = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.organization_id == ctx.organization_id)
        )
    ).scalar_one()
    assert proposal.action == "restart-wazuh"


@pytest.mark.asyncio
async def test_propose_refused_when_credential_lacks_capability(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, {}),  # empty policies → fail closed
        ProposeActiveResponseInput(
            agent_id="001", intent="block_ip", srcip="203.0.113.7", rationale="x"
        ),
    )
    assert out.permitted is False
    assert "not authorized" in out.summary.lower() or "not authorized" in out.detail.lower()


@pytest.mark.asyncio
async def test_propose_rejected_when_block_command_missing_srcip(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    """block_ip with no srcip is refused by the validator BEFORE the queue (the
    selected command would be a no-op on the host — the AR script needs
    data.srcip)."""
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW),
        ProposeActiveResponseInput(agent_id="001", intent="block_ip", rationale="x"),
    )
    assert out.permitted is False
    assert out.state == "rejected"
    assert "srcip" in out.detail.lower()


@pytest.mark.asyncio
async def test_propose_allowed_when_agent_in_granted_group(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    """Per-org case: AR granted on agent:group:acme + target agent IS in acme →
    the proposal is accepted (the 6-a.1 fix; an id-only check would refuse it)."""
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW_BY_GROUP, agent_groups=["default", "acme"]),
        ProposeActiveResponseInput(
            agent_id="001", intent="block_ip", srcip="203.0.113.7", rationale="x"
        ),
    )
    assert out.permitted is True
    assert out.state == "pending"


@pytest.mark.asyncio
async def test_propose_refused_when_agent_not_in_granted_group(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    """Cross-group: AR granted on agent:group:acme but the target agent is only
    in 'beta' → refused at the pre-flight."""
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW_BY_GROUP, agent_groups=["default", "beta"]),
        ProposeActiveResponseInput(
            agent_id="001", intent="block_ip", srcip="203.0.113.7", rationale="x"
        ),
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
        _exec_ctx(db, ctx, _ALLOW),  # default stub OS = Ubuntu → firewall-drop
        ProposeActiveResponseInput(
            agent_id="001",
            intent="block_ip",
            srcip="203.0.113.7",
            rationale="brute force",
            alert_ids=["a1"],
        ),
    )
    assert out.permitted is True
    assert out.state == "pending"
    assert out.proposal_id
    # The propose tool emits a citation so its call surfaces in the Evidence panel.
    assert out.citation.tool == "propose_active_response"
    assert out.citation.result_count == 1

    proposal = (
        await db.execute(
            select(ActionProposal).where(
                ActionProposal.organization_id == ctx.organization_id
            )
        )
    ).scalar_one()
    assert proposal.state == ProposalState.pending
    assert proposal.action == "firewall-drop"  # Wolf selected it from the Linux OS
    assert proposal.parameters.get("intent") == "block_ip"
    assert proposal.parameters.get("srcip") == "203.0.113.7"
    assert proposal.requested_by == ctx.user_id
