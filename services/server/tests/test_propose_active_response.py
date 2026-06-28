"""propose_active_response tool — Phase 6 (ADR 0025), intent-driven (slice 6-c).

The model expresses a high-level INTENT (block_ip / disable_user / restart) and
Wolf resolves the agent's OS to deterministically pick the platform-correct
command. The tool must: reject an unknown intent, select the right command per
OS (block_ip → firewall-drop on Linux, netsh on Windows), refuse when the OS is
unknown / the intent is unsupported on the OS, refuse when the credential's RBAC
doesn't permit the action (capability pre-flight), and on success persist a
*pending* proposal carrying the resolved command — never execute.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_server.gateway.models import ActionProposal, ProposalState
from wolf_server.guardrails.limits import DEFAULT_LIMITS
from wolf_server.organization.context import OrganizationContext
from wolf_server.tools.active_blocks import ListActiveBlocksInput, ListActiveBlocksTool
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


async def _seed_block(
    db: AsyncSession,
    ctx: OrganizationContext,
    *,
    srcip: str | None = None,
    username: str | None = None,
    command: str = "firewall-drop",
    rationale: str = "brute-force auth (rule 5710)",
    agent_id: str = "001",
    alert_ids: list[str] | None = None,
    state: ProposalState = ProposalState.succeeded,
) -> ActionProposal:
    """Insert a (succeeded) block proposal so the reversal path has a ledger row
    to recall + link (ADR 0028)."""
    now = datetime.now(UTC)
    params: dict[str, Any] = {
        "intent": "block_ip" if srcip else "disable_user",
        "agent_os": "Ubuntu 22.04",
    }
    if srcip:
        params["srcip"] = srcip
    if username:
        params["username"] = username
    block = ActionProposal(
        organization_id=ctx.organization_id,
        action_class="active_response",
        target={"agent_id": agent_id},
        action=command,
        parameters=params,
        rationale=rationale,
        evidence={"alert_ids": alert_ids or []},
        expected_effect="block",
        rollback_plan=None,
        severity="high",
        requested_by=ctx.user_id,
        content_hash="0" * 64,
        state=state,
        executed_at=now,
        created_at=now,
        expires_at=now,
    )
    db.add(block)
    await db.flush()
    return block


@pytest.mark.asyncio
async def test_propose_unblock_recalls_and_links_the_block(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    """unblock_ip finds the active block, recalls its reason/evidence, links the
    reversal, and stamps the block (ADR 0028 provenance recall)."""
    ctx = _ctx(seed_organization_and_user)
    block = await _seed_block(db, ctx, srcip="203.0.113.7", alert_ids=["a1", "a2"])
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW),
        ProposeActiveResponseInput(agent_id="001", intent="unblock_ip", srcip="203.0.113.7"),
    )
    assert out.permitted is True
    assert "brute-force auth" in out.summary  # recalled reason surfaced to the user
    reversal = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.reverses_proposal_id == block.id)
        )
    ).scalar_one()
    assert reversal.action == "firewall-drop"  # the same command the block used
    assert reversal.parameters.get("reversal") is True
    assert reversal.evidence.get("original_rationale") == block.rationale
    await db.refresh(block)
    assert block.reversal_proposal_id == reversal.id  # block stamped, won't double-fire


@pytest.mark.asyncio
async def test_propose_unblock_reverses_the_exact_block_command(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    """If the block used host-deny (not the OS default), the unblock reverses
    host-deny — the undo matches what was actually done."""
    ctx = _ctx(seed_organization_and_user)
    block = await _seed_block(db, ctx, srcip="198.51.100.9", command="host-deny")
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW),
        ProposeActiveResponseInput(agent_id="001", intent="unblock_ip", srcip="198.51.100.9"),
    )
    assert out.permitted is True
    reversal = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.reverses_proposal_id == block.id)
        )
    ).scalar_one()
    assert reversal.action == "host-deny"
    assert reversal.parameters.get("reversal") is True


@pytest.mark.asyncio
async def test_propose_unblock_refused_when_no_block_on_record(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW),
        ProposeActiveResponseInput(agent_id="001", intent="unblock_ip", srcip="203.0.113.7"),
    )
    assert out.permitted is False
    assert out.state == "rejected"
    assert "no record" in out.detail.lower() or "no matching" in out.summary.lower()


@pytest.mark.asyncio
async def test_propose_unblock_refuses_a_method(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    await _seed_block(db, ctx, srcip="203.0.113.7")
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW),
        ProposeActiveResponseInput(
            agent_id="001", intent="unblock_ip", srcip="203.0.113.7", method="host-deny"
        ),
    )
    assert out.permitted is False
    assert "method" in out.detail.lower()


@pytest.mark.asyncio
async def test_propose_block_surfaces_existing_block_as_dedup_context(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    await _seed_block(db, ctx, srcip="203.0.113.7", rationale="prior scan")
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW),
        ProposeActiveResponseInput(
            agent_id="001", intent="block_ip", srcip="203.0.113.7", rationale="again"
        ),
    )
    assert out.permitted is True  # not silently blocked, but...
    assert "already has an active block" in out.summary  # ...surfaced as context


@pytest.mark.asyncio
async def test_propose_timed_block_records_duration_seconds(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW),
        ProposeActiveResponseInput(
            agent_id="001", intent="block_ip", srcip="203.0.113.7",
            block_duration="1h", rationale="x",
        ),
    )
    assert out.permitted is True
    proposal = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.organization_id == ctx.organization_id)
        )
    ).scalar_one()
    assert proposal.parameters.get("block_duration_seconds") == 3600


@pytest.mark.asyncio
async def test_propose_duration_refused_on_non_reversible_restart(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW),
        ProposeActiveResponseInput(
            agent_id="001", intent="restart", block_duration="1h", rationale="x"
        ),
    )
    assert out.permitted is False
    assert "not reversible" in out.detail.lower()


@pytest.mark.asyncio
async def test_propose_invalid_duration_refused(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW),
        ProposeActiveResponseInput(
            agent_id="001", intent="block_ip", srcip="203.0.113.7",
            block_duration="soon", rationale="x",
        ),
    )
    assert out.permitted is False
    assert "duration" in out.detail.lower()


@pytest.mark.asyncio
async def test_list_active_blocks_tool_surfaces_ledger_with_reason(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    """The read tool reports Wolf's dispatch ledger (IP, agent, reason) with an
    honest 'not a live host check' note (ADR 0028)."""
    ctx = _ctx(seed_organization_and_user)
    await _seed_block(db, ctx, srcip="203.0.113.7", rationale="brute-force auth")
    out = await ListActiveBlocksTool().run(_exec_ctx(db, ctx, _ALLOW), ListActiveBlocksInput())
    assert len(out.blocks) == 1
    block = out.blocks[0]
    assert block.target == "203.0.113.7"
    assert block.target_kind == "srcip"
    assert block.reason == "brute-force auth"
    assert "not a live host" in out.note.lower()


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
async def test_propose_block_ip_freebsd_selects_pf(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    """6-c.2a: block_ip on a (generic) FreeBSD agent auto-selects `pf`."""
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW, os_platform="FreeBSD 14.3-RELEASE"),
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
    assert proposal.action == "pf"


@pytest.mark.asyncio
async def test_propose_block_ip_opnsense_selects_opnsense_fw(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    """6-c.2a: the live agent 009 signal (os.platform=bsd, uname FreeBSD…OPNsense)
    is detected as the OPNsense appliance → opnsense-fw, NOT stock pf."""
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW, os_platform="bsd FreeBSD OPNsense.internal 14.3-RELEASE"),
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
    assert proposal.action == "opnsense-fw"


@pytest.mark.asyncio
async def test_propose_method_override_uses_named_command(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    """6-c.2b: an explicit `method` overrides the auto-default (host-deny instead
    of firewall-drop on Linux), recorded as method_source=override."""
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW),  # default stub OS = Ubuntu (Linux)
        ProposeActiveResponseInput(
            agent_id="001",
            intent="block_ip",
            srcip="203.0.113.7",
            method="host-deny",
            rationale="x",
        ),
    )
    assert out.permitted is True
    proposal = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.organization_id == ctx.organization_id)
        )
    ).scalar_one()
    assert proposal.action == "host-deny"
    assert proposal.parameters.get("method_source") == "override"


@pytest.mark.asyncio
async def test_propose_method_override_refused_on_platform_mismatch(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW),  # Linux agent
        ProposeActiveResponseInput(
            agent_id="001", intent="block_ip", srcip="203.0.113.7", method="netsh", rationale="x"
        ),
    )
    assert out.permitted is False
    assert "windows" in out.detail.lower()


@pytest.mark.asyncio
async def test_propose_os_unknown_failover_with_method(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    """6-c.2b failover: OS can't be determined, but the human asserts the method —
    Wolf proposes it (method_source=user_asserted), approval still the gate."""
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW, os_platform=None),  # OS unknown
        ProposeActiveResponseInput(
            agent_id="001", intent="block_ip", srcip="203.0.113.7", method="pf", rationale="x"
        ),
    )
    assert out.permitted is True
    proposal = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.organization_id == ctx.organization_id)
        )
    ).scalar_one()
    assert proposal.action == "pf"
    assert proposal.parameters.get("method_source") == "user_asserted"


@pytest.mark.asyncio
async def test_propose_succeeds_without_rationale_and_records_placeholder(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    """Bug fix: the model frequently omits the (now optional) rationale; the
    proposal must still queue, with an honest placeholder rationale recorded."""
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeActiveResponseTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW),
        ProposeActiveResponseInput(agent_id="001", intent="block_ip", srcip="203.0.113.7"),
    )
    assert out.permitted is True
    assert out.state == "pending"
    proposal = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.organization_id == ctx.organization_id)
        )
    ).scalar_one()
    assert "no explicit rationale" in proposal.rationale.lower()


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
