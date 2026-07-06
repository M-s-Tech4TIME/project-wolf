"""propose_config_change tool — 6-e.4, generalized in 6-f.4 (ADR 0032 B).

The tool validates the section (blocklist + shape + block-identity) + op,
capability-pre-flights ``manager:update_config`` (Superuser-scoped, manager-global),
captures the CURRENT content (the approver's diff base + staleness check), and
dry-runs the exact transformation.  The flow is TWO-PHASE (B1 confirm-diff): a
call without ``user_confirmed`` returns a ``needs_confirmation`` PREVIEW with the
current content and queues NOTHING; only the confirmed re-call queues a *pending*
proposal — never executes.  ``restore_config`` for a target with an active prior
Wolf change is the UNDO: linked + recalled (block-identity aware).
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
  <integration>
    <name>slack</name>
    <hook_url>https://hooks.example.invalid/services/T0</hook_url>
  </integration>
</ossec_config>
"""

_VT_BLOCK = (
    "<integration><name>virustotal</name><api_key>KEY</api_key>"
    "<group>syscheck</group><alert_format>json</alert_format></integration>"
)


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
async def test_unconfirmed_call_returns_a_preview_and_queues_nothing(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    # Phase 1 of the confirm-diff loop (B1): no user_confirmed → PREVIEW with the
    # live current content; NOTHING enters the queue.
    ctx = _ctx(seed_organization_and_user)
    out = await ProposeConfigChangeTool().run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG),
        ProposeConfigChangeInput(
            section="sca",
            operation="update_section",
            section_content="<sca><enabled>no</enabled></sca>",
        ),
    )
    assert out.permitted is False
    assert out.state == "needs_confirmation"
    assert out.proposal_id == ""
    assert "<interval>12h</interval>" in out.current_content  # the live block, for the diff
    assert "user_confirmed=true" in out.summary
    rows = (await db.execute(select(ActionProposal))).scalars().all()
    assert all(r.action_class != "config_change" for r in rows)


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
            user_confirmed=True,
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
async def test_refused_for_blocked_section(
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
async def test_absent_section_becomes_an_add(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    # 6-f.4 (B3): an absent single-instance section is an ADD, not a refusal.
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeConfigChangeTool()
    preview = await tool.run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG),
        ProposeConfigChangeInput(
            section="remote", operation="update_section", section_content="<remote></remote>"
        ),
    )
    assert preview.state == "needs_confirmation"
    assert preview.current_content == ""  # nothing exists yet — this adds
    assert "add <remote>" in preview.summary
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG),
        ProposeConfigChangeInput(
            section="remote",
            operation="update_section",
            section_content="<remote></remote>",
            user_confirmed=True,
        ),
    )
    assert out.permitted is True
    row = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.id == uuid.UUID(out.proposal_id))
        )
    ).scalar_one()
    assert row.parameters["current_content"] == ""


@pytest.mark.asyncio
async def test_refused_when_section_repeated_without_identity(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    # <global> appears twice and has no identity key — ambiguous, refused.
    ctx = _ctx(seed_organization_and_user)
    out = await ProposeConfigChangeTool().run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG),
        ProposeConfigChangeInput(
            section="global",
            operation="update_section",
            section_content="<global><logall>yes</logall></global>",
            user_confirmed=True,
        ),
    )
    assert out.permitted is False
    assert "more than once" in out.summary or "ambiguous" in out.detail


@pytest.mark.asyncio
async def test_repeated_identity_section_guides_to_upsert_block(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    # update_section on a repeated IDENTITY section points at the right op.
    doubled = _OSSEC.replace(
        "</ossec_config>",
        "<integration><name>shuffle</name></integration>\n</ossec_config>",
    )
    ctx = _ctx(seed_organization_and_user)
    out = await ProposeConfigChangeTool().run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG, raw=doubled),
        ProposeConfigChangeInput(
            section="integration",
            operation="update_section",
            section_content=_VT_BLOCK,
            user_confirmed=True,
        ),
    )
    assert out.permitted is False
    assert "upsert_block" in out.detail


# Three <integration> blocks sharing one <name> — the operator's live tracecat
# web-test scenario (2026-07-06): only <hook_url>/<api_key> distinguish them.
_TRACECAT_DUPES = """<ossec_config>
  <integration>
    <name>custom-tracecat</name>
    <hook_url>https://tc.example.invalid/hook/AAA</hook_url>
    <api_key>key-AAA</api_key>
    <level>5</level>
  </integration>
  <integration>
    <name>custom-tracecat</name>
    <hook_url>https://tc.example.invalid/hook/BBB</hook_url>
    <api_key>key-BBB</api_key>
    <level>5</level>
  </integration>
  <integration>
    <name>custom-tracecat</name>
    <hook_url>https://tc.example.invalid/hook/CCC</hook_url>
    <api_key>key-CCC</api_key>
    <level>5</level>
  </integration>
</ossec_config>
"""


@pytest.mark.asyncio
async def test_ambiguous_key_refusal_enumerates_discriminating_fields(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    # 6-f.5: the refusal must TEACH the fix — list each instance's unique
    # fields so the model re-addresses precisely instead of hallucinating.
    ctx = _ctx(seed_organization_and_user)
    out = await ProposeConfigChangeTool().run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG, raw=_TRACECAT_DUPES),
        ProposeConfigChangeInput(
            section="integration",
            operation="upsert_block",
            block_key="custom-tracecat",
            section_content=(
                "<integration><name>custom-tracecat</name>"
                "<hook_url>https://tc.example.invalid/hook/BBB</hook_url>"
                "<api_key>key-BBB</api_key><level>3</level></integration>"
            ),
        ),
    )
    assert out.permitted is False
    assert out.state == "rejected"
    assert "https://tc.example.invalid/hook/AAA" in out.detail
    assert "https://tc.example.invalid/hook/CCC" in out.detail
    assert "Re-call with 'block_key'" in out.detail


@pytest.mark.asyncio
async def test_upsert_by_unique_hook_url_selects_among_same_name_instances(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    # The smart path the operator expected: address the ONE instance by its
    # unique <hook_url> even though all three share a <name>.
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeConfigChangeTool()
    key = "https://tc.example.invalid/hook/BBB"
    content = (
        "<integration><name>custom-tracecat</name>"
        f"<hook_url>{key}</hook_url>"
        "<api_key>key-BBB</api_key><level>3</level></integration>"
    )
    preview = await tool.run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG, raw=_TRACECAT_DUPES),
        ProposeConfigChangeInput(
            section="integration",
            operation="upsert_block",
            block_key=key,
            section_content=content,
        ),
    )
    assert preview.state == "needs_confirmation"
    assert "key-BBB" in preview.current_content  # the addressed instance is the diff base
    assert "key-AAA" not in preview.current_content
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG, raw=_TRACECAT_DUPES),
        ProposeConfigChangeInput(
            section="integration",
            operation="upsert_block",
            block_key=key,
            section_content=content,
            user_confirmed=True,
        ),
    )
    assert out.permitted is True
    row = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.id == uuid.UUID(out.proposal_id))
        )
    ).scalar_one()
    assert row.target == {"section": "integration", "block_key": key}
    assert "key-BBB" in row.parameters["current_content"]


@pytest.mark.asyncio
async def test_upsert_block_adds_a_new_integration(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    # THE virustotal case (ADR 0032 B2): one <integration> exists (slack);
    # adding virustotal addresses the new instance by its <name>.
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeConfigChangeTool()
    preview = await tool.run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG),
        ProposeConfigChangeInput(
            section="integration",
            operation="upsert_block",
            block_key="virustotal",
            section_content=_VT_BLOCK,
        ),
    )
    assert preview.state == "needs_confirmation"
    assert preview.current_content == ""  # no virustotal block exists yet
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG),
        ProposeConfigChangeInput(
            section="integration",
            operation="upsert_block",
            block_key="virustotal",
            section_content=_VT_BLOCK,
            rationale="enable VirusTotal file-hash lookups for FIM alerts",
            user_confirmed=True,
        ),
    )
    assert out.permitted is True
    assert out.state == "pending"
    row = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.id == uuid.UUID(out.proposal_id))
        )
    ).scalar_one()
    assert row.action == "upsert_block"
    assert row.target == {"section": "integration", "block_key": "virustotal"}
    assert row.parameters["current_content"] == ""
    assert row.parameters["section_content"] == _VT_BLOCK


@pytest.mark.asyncio
async def test_upsert_block_updates_the_keyed_instance_with_diff_base(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    new_slack = (
        "<integration><name>slack</name>"
        "<hook_url>https://hooks.example.invalid/services/NEW</hook_url></integration>"
    )
    out = await ProposeConfigChangeTool().run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG),
        ProposeConfigChangeInput(
            section="integration",
            operation="upsert_block",
            block_key="slack",
            section_content=new_slack,
            user_confirmed=True,
        ),
    )
    assert out.permitted is True
    row = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.id == uuid.UUID(out.proposal_id))
        )
    ).scalar_one()
    # the LIVE slack block was captured as the diff base
    assert "services/T0" in row.parameters["current_content"]


@pytest.mark.asyncio
async def test_remove_block_requires_an_existing_instance(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    ctx = _ctx(seed_organization_and_user)
    tool = ProposeConfigChangeTool()
    missing = await tool.run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG),
        ProposeConfigChangeInput(
            section="integration",
            operation="remove_block",
            block_key="virustotal",
            user_confirmed=True,
        ),
    )
    assert missing.permitted is False
    assert "nothing to remove" in missing.detail.lower()
    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG),
        ProposeConfigChangeInput(
            section="integration",
            operation="remove_block",
            block_key="slack",
            user_confirmed=True,
        ),
    )
    assert out.permitted is True
    row = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.id == uuid.UUID(out.proposal_id))
        )
    ).scalar_one()
    assert row.action == "remove_block"
    assert "services/T0" in row.parameters["current_content"]  # what gets removed
    assert "section_content" not in row.parameters


@pytest.mark.asyncio
async def test_upsert_block_refuses_identity_mismatch(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    # addressing 'virustotal' while the content names 'slack' — the validator's
    # X-for-Y guard, exercised through the tool.
    ctx = _ctx(seed_organization_and_user)
    out = await ProposeConfigChangeTool().run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG),
        ProposeConfigChangeInput(
            section="integration",
            operation="upsert_block",
            block_key="virustotal",
            section_content="<integration><name>slack</name></integration>",
            user_confirmed=True,
        ),
    )
    assert out.permitted is False
    assert "must identify" in out.detail


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
async def test_restore_config_matches_the_block_key(
    db: AsyncSession, seed_organization_and_user: dict[str, Any]
) -> None:
    # 6-f.4: with a block_key the undo targets THAT instance's change — a prior
    # change on a different key is not matched.
    ctx = _ctx(seed_organization_and_user)
    prior = ActionProposal(
        organization_id=ctx.organization_id,
        action_class="config_change",
        target={"section": "integration", "block_key": "virustotal"},
        action="upsert_block",
        parameters={"section_content": _VT_BLOCK, "current_content": ""},
        rationale="original: add virustotal",
        evidence={},
        expected_effect="virustotal integration added",
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

    tool = ProposeConfigChangeTool()
    wrong_key = await tool.run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG),
        ProposeConfigChangeInput(
            section="integration", operation="restore_config", block_key="slack"
        ),
    )
    assert wrong_key.permitted is False
    assert "Nothing to undo" in wrong_key.summary

    out = await tool.run(
        _exec_ctx(db, ctx, _ALLOW_CONFIG),
        ProposeConfigChangeInput(
            section="integration", operation="restore_config", block_key="virustotal"
        ),
    )
    assert out.permitted is True
    assert "original: add virustotal" in out.summary
    reversal = (
        await db.execute(
            select(ActionProposal).where(ActionProposal.id == uuid.UUID(out.proposal_id))
        )
    ).scalar_one()
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
