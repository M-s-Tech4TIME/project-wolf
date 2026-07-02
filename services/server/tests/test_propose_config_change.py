"""propose_config_change tool — Phase 6-e.4 (ADR 0029).

The tool validates the section (allowlist + single-instance) + op + block shape,
capability-pre-flights ``manager:update_config`` (Superuser-scoped, manager-global),
captures the CURRENT section content (the approver's diff base + staleness check),
and on success queues a *pending* proposal — never executes. ``restore_config``
for a section with an active prior Wolf change is the UNDO: linked + recalled.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_server.gateway.models import ActionProposal, ProposalState
from wolf_server.guardrails.limits import DEFAULT_LIMITS
from wolf_server.organization.context import OrganizationContext
from wolf_server.tools.base import ToolExecContext
from wolf_server.tools.propose_config_change import (
    ProposeConfigChangeInput,
    ProposeConfigChangeTool,
)
from wolf_server.wazuh.capabilities import ACTION_UPDATE_MANAGER_CONFIG

_ALLOW_CONFIG = {ACTION_UPDATE_MANAGER_CONFIG: {"*:*:*": "allow"}}
_DENY: dict[str, dict[str, str]] = {}

_OSSEC = """<ossec_config>
  <sca>
    <enabled>yes</enabled>
    <interval>12h</interval>
  </sca>
  <global><logall>no</logall></global>
  <global><jsonout_output>yes</jsonout_output></global>
</ossec_config>
"""


class _StubServerApi:
    """Serves the two reads the propose path makes: effective policies (get) and
    the raw ossec.conf (get_raw)."""

    def __init__(self, policies: dict[str, dict[str, str]], *, raw: str = _OSSEC) -> None:
        self._policies = policies
        self._raw = raw

    async def get(self, path: str, *, params: Any = None) -> Any:
        return {"data": self._policies}

    async def get_raw(self, path: str, *, params: Any = None) -> str:
        return self._raw


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
    *,
    raw: str = _OSSEC,
) -> ToolExecContext:
    return ToolExecContext(
        organization=ctx,
        limits=DEFAULT_LIMITS,
        opensearch=None,
        server_api=_StubServerApi(policies, raw=raw),
        db=db,
    )


@pytest.mark.asyncio
async def test_update_section_queues_pending_with_diff_base(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeConfigChangeTool()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG),
        ProposeConfigChangeInput(
            section="sca",
            operation="update_section",
            section_content="<sca><enabled>no</enabled></sca>",
            rationale="SCA too noisy for this fleet",
        ),
    )
    assert out.permitted is True
    assert out.state == "pending"
    row = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.id == uuid.UUID(out.proposal_id))
        )
    ).scalar_one()
    assert row.action_class == "config_change"
    assert row.action == "update_section"
    assert row.target == {"section": "sca"}
    assert row.parameters["section_content"] == "<sca><enabled>no</enabled></sca>"
    # The CURRENT section content was captured as the approver's diff base.
    assert "<interval>12h</interval>" in row.parameters["current_content"]
    assert row.severity == "high"
    assert row.state == ProposalState.pending


@pytest.mark.asyncio
async def test_refused_without_manager_update_config(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    out = await ProposeConfigChangeTool().run(
        _exec_ctx(db, ctx, _DENY),
        ProposeConfigChangeInput(
            section="sca", operation="update_section", section_content="<sca></sca>"
        ),
    )
    assert out.permitted is False
    assert out.state == "rejected"
    assert "manager:update_config" in out.detail


@pytest.mark.asyncio
async def test_refused_for_non_allowlisted_section(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    out = await ProposeConfigChangeTool().run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG),
        ProposeConfigChangeInput(
            section="cluster", operation="update_section", section_content="<cluster></cluster>"
        ),
    )
    assert out.permitted is False
    assert "not editable" in out.detail


@pytest.mark.asyncio
async def test_refused_when_section_absent_or_repeated(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeConfigChangeTool()
    # 'remote' is allowlisted but not present in this stub ossec.conf.
    absent = await tool.run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG),
        ProposeConfigChangeInput(
            section="remote", operation="update_section", section_content="<remote></remote>"
        ),
    )
    assert absent.permitted is False
    assert "not present" in absent.detail or "not in the current" in absent.summary


@pytest.mark.asyncio
async def test_restore_config_undoes_active_prior_change(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    # Seed a succeeded prior config change on <sca>.
    prior = ActionProposal(
        organization_id=ctx.organization_id,
        action_class="config_change",
        target={"section": "sca"},
        action="update_section",
        parameters={"section_content": "<sca><enabled>no</enabled></sca>"},
        rationale="original: silence SCA",
        evidence={},
        expected_effect="disable sca",
        rollback_plan=None,
        severity="high",
        requested_by=ctx.user_id,
        content_hash=f"seed-{uuid.uuid4().hex}",
        state=ProposalState.succeeded,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC),
        executed_at=datetime.now(UTC),
    )
    db.add(prior)
    await db.flush()

    out = await ProposeConfigChangeTool().run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG),
        ProposeConfigChangeInput(section="sca", operation="restore_config"),
    )
    assert out.permitted is True
    assert out.state == "pending"
    assert "original: silence SCA" in out.summary  # recalled the original reason
    reversal = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.id == uuid.UUID(out.proposal_id))
        )
    ).scalar_one()
    assert reversal.action == "restore_config"
    assert reversal.reverses_proposal_id == prior.id


@pytest.mark.asyncio
async def test_restore_config_with_no_prior_is_refused(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    out = await ProposeConfigChangeTool().run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG),
        ProposeConfigChangeInput(section="syscheck", operation="restore_config"),
    )
    assert out.permitted is False
    assert "Nothing to undo" in out.summary
