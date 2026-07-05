"""config_change execution + snapshot-restore reversal — Phase 6-e.4 (ADR 0029).

The config_change executor replaces ONE allowlisted section in ossec.conf and
APPLIES it (validate -> cluster restart) with auto-rollback if the edited config
does not validate.  It AUTHORITATIVELY confirms the new block actually persisted
(``GET`` reflects the on-disk file immediately) before declaring success — a
phantom no-op fails honestly.  Freshness refuses a STALE proposal (the section
changed under it).  The reversal restores the captured whole-file snapshot and
hash-verifies it.  In-memory stubs share a ``_Disk`` so a read-back reflects what
was written — no live manager.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_server.gateway.executors import ConfigValidationError, get_executor
from wolf_server.gateway.models import ActionProposal, ProposalState
from wolf_server.gateway.proposals import create_proposal
from wolf_server.gateway.reversal import REVERSAL_STATE_COMPLETED
from wolf_server.wazuh.capabilities import (
    ACTION_CLUSTER_RESTART,
    ACTION_UPDATE_MANAGER_CONFIG,
    CredentialCapabilities,
)

_ORG = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_REQUESTER = uuid.UUID("11111111-1111-1111-1111-111111111111")
_CAPS = CredentialCapabilities(
    policies={
        ACTION_UPDATE_MANAGER_CONFIG: {"*:*:*": "allow"},
        ACTION_CLUSTER_RESTART: {"*:*:*": "allow"},
    }
)

_SCA_CURRENT = "<sca>\n    <enabled>yes</enabled>\n    <interval>12h</interval>\n  </sca>"
_SCA_NEW = "<sca><enabled>no</enabled></sca>"
_OSSEC = f"""<ossec_config>
  {_SCA_CURRENT}
  <syscheck><frequency>43200</frequency></syscheck>
</ossec_config>
"""


class _Disk:
    def __init__(self, content: str) -> None:
        self.content = content


class _StubReadApi:
    def __init__(self, disk: _Disk, *, validation_ok: bool = True) -> None:
        self.disk = disk
        self.validation_ok = validation_ok

    async def get(self, path: str, *, params: Any = None) -> dict[str, Any]:
        if path == "/manager/configuration/validation":
            if self.validation_ok:
                return {
                    "data": {
                        "affected_items": [{"name": "m", "status": "OK"}],
                        "total_failed_items": 0,
                    }
                }
            return {
                "data": {
                    "affected_items": [{"name": "m", "status": "ERROR"}],
                    "total_failed_items": 1,
                    "failed_items": [{"error": "bad xml"}],
                }
            }
        return {"data": {}}

    async def get_raw(self, path: str, *, params: Any = None) -> str:
        return self.disk.content


class _StubActionApi:
    def __init__(
        self,
        disk: _Disk,
        *,
        persist: bool = True,
        reformat: Callable[[str], str] | None = None,
    ) -> None:
        self.disk = disk
        self.persist = persist
        # Simulates the manager re-serialising ossec.conf on write (it re-indents
        # to its own house style) — the read-back is NOT byte-identical to what
        # was PUT. Used to reproduce the live 6-e.4 persist false-negative.
        self.reformat = reformat
        self.writes: list[str] = []
        self.restarts = 0

    async def update_manager_configuration(
        self, *, content: str, capabilities: Any
    ) -> dict[str, Any]:
        self.writes.append(content)
        if self.persist:
            self.disk.content = self.reformat(content) if self.reformat else content
        return {"data": {"affected_items": ["ossec.conf"]}}

    async def restart_cluster(self, *, capabilities: Any) -> dict[str, Any]:
        self.restarts += 1
        return {"data": {"affected_items": ["m"]}}


async def _proposal(
    db: AsyncSession,
    *,
    action: str,
    parameters: dict[str, Any],
    reverses: uuid.UUID | None = None,
    target: dict[str, Any] | None = None,
) -> ActionProposal:
    from wolf_server.gateway.executors import ExecContext  # noqa: F401 — keep import graph warm

    p = await create_proposal(
        db,
        organization_id=_ORG,
        requested_by=_REQUESTER,
        action_class="config_change",
        target=target or {"section": "sca"},
        action=action,
        parameters=parameters,
        rationale="tune sca",
        expected_effect="update",
        reverses_proposal_id=reverses,
    )
    p.state = ProposalState.succeeded
    p.executed_at = datetime.now(UTC)
    await db.flush()
    return p


def _ctx(
    db: AsyncSession,
    disk: _Disk,
    *,
    validation_ok: bool = True,
    persist: bool = True,
    reformat: Callable[[str], str] | None = None,
):
    from wolf_server.gateway.executors import ExecContext

    return ExecContext(
        read_api=_StubReadApi(disk, validation_ok=validation_ok),
        action_api=_StubActionApi(disk, persist=persist, reformat=reformat),
        capabilities=_CAPS,
        db=db,
    )


@pytest.mark.asyncio
async def test_forward_replaces_section_validates_confirms_and_restarts(db: AsyncSession) -> None:
    proposal = await _proposal(
        db,
        action="update_section",
        parameters={"section_content": _SCA_NEW, "current_content": _SCA_CURRENT},
    )
    disk = _Disk(_OSSEC)
    ctx = _ctx(db, disk)

    freshness, perform, verify = get_executor("config_change").build_forward(proposal, ctx)
    assert (await freshness(proposal))[0] is True

    res = await perform(proposal)
    assert len(ctx.action_api.writes) == 1  # no rollback
    assert ctx.action_api.restarts == 1
    written = ctx.action_api.writes[0]
    assert "<enabled>no</enabled>" in written
    assert "<interval>12h</interval>" not in written  # old sca body replaced
    assert "<frequency>43200</frequency>" in written  # other section untouched
    # prior_state captured for the undo (whole file).
    assert proposal.prior_state is not None
    assert proposal.prior_state["content"] == _OSSEC
    assert res["section_updated"] is True

    ok, detail = await verify(proposal, res)
    assert ok is True
    assert detail["section"] == "sca"
    assert detail["restart_issued"] is True


@pytest.mark.asyncio
async def test_forward_confirms_persist_despite_manager_reformatting(db: AsyncSession) -> None:
    # Reproduces the live 6-e.4 failure: the manager re-indents ossec.conf on
    # write, so the <sca> block read back is not a byte-for-byte substring of what
    # Wolf PUT. The change DID apply — the reformatting-tolerant persist check
    # must confirm success. The old literal `new_block in reread` check
    # false-failed here (twice, on the operator's cluster) and rolled back.
    proposal = await _proposal(
        db,
        action="update_section",
        parameters={"section_content": _SCA_NEW, "current_content": _SCA_CURRENT},
    )

    def _reindent(content: str) -> str:
        return content.replace("><", ">\n      <")  # manager-style layout

    disk = _Disk(_OSSEC)
    ctx = _ctx(db, disk, reformat=_reindent)

    _f, perform, verify = get_executor("config_change").build_forward(proposal, ctx)
    res = await perform(proposal)
    assert len(ctx.action_api.writes) == 1  # applied once, no rollback restore
    assert ctx.action_api.restarts == 1  # reached the apply step
    assert res["section_updated"] is True
    assert "<enabled>no</enabled>" in disk.content  # the change really landed
    ok, _detail = await verify(proposal, res)
    assert ok is True


@pytest.mark.asyncio
async def test_forward_auto_rolls_back_on_validation_failure(db: AsyncSession) -> None:
    proposal = await _proposal(
        db,
        action="update_section",
        parameters={"section_content": _SCA_NEW, "current_content": _SCA_CURRENT},
    )
    disk = _Disk(_OSSEC)
    ctx = _ctx(db, disk, validation_ok=False)

    _f, perform, _v = get_executor("config_change").build_forward(proposal, ctx)
    with pytest.raises(ConfigValidationError):
        await perform(proposal)
    # bad edit written, then prior restored — two writes, NO restart.
    assert len(ctx.action_api.writes) == 2
    assert ctx.action_api.writes[1] == _OSSEC
    assert ctx.action_api.restarts == 0


@pytest.mark.asyncio
async def test_forward_fails_honestly_if_change_does_not_persist(db: AsyncSession) -> None:
    proposal = await _proposal(
        db,
        action="update_section",
        parameters={"section_content": _SCA_NEW, "current_content": _SCA_CURRENT},
    )
    disk = _Disk(_OSSEC)
    ctx = _ctx(db, disk, persist=False)  # writes "succeed" but don't stick

    _f, perform, _v = get_executor("config_change").build_forward(proposal, ctx)
    with pytest.raises(ConfigValidationError, match="did not persist"):
        await perform(proposal)
    assert ctx.action_api.restarts == 0  # never applied a phantom change


@pytest.mark.asyncio
async def test_forward_freshness_refuses_stale_proposal(db: AsyncSession) -> None:
    # The live section no longer matches what the approver reviewed → stale.
    proposal = await _proposal(
        db,
        action="update_section",
        parameters={"section_content": _SCA_NEW, "current_content": _SCA_CURRENT},
    )
    drifted = _OSSEC.replace("<interval>12h</interval>", "<interval>6h</interval>")
    ctx = _ctx(db, _Disk(drifted))
    freshness, _p, _v = get_executor("config_change").build_forward(proposal, ctx)
    ok, reason = await freshness(proposal)
    assert ok is False
    assert "changed since" in reason


@pytest.mark.asyncio
async def test_forward_freshness_refuses_repeated_section(db: AsyncSession) -> None:
    proposal = await _proposal(
        db,
        action="update_section",
        parameters={"section_content": _SCA_NEW, "current_content": _SCA_CURRENT},
    )
    doubled = _OSSEC.replace("</ossec_config>", f"  {_SCA_CURRENT}\n</ossec_config>")
    ctx = _ctx(db, _Disk(doubled))
    freshness, _p, _v = get_executor("config_change").build_forward(proposal, ctx)
    ok, reason = await freshness(proposal)
    assert ok is False
    assert "exactly once" in reason


# ── block-identity ops (6-f.4, ADR 0032 B2) ─────────────────────────────────

_SLACK_BLOCK = (
    "<integration>\n    <name>slack</name>\n"
    "    <hook_url>https://hooks.example.invalid/services/T0</hook_url>\n  </integration>"
)
_VT_BLOCK = (
    "<integration><name>virustotal</name><api_key>KEY</api_key>"
    "<group>syscheck</group></integration>"
)
_OSSEC_INTEG = f"""<ossec_config>
  {_SCA_CURRENT}
  {_SLACK_BLOCK}
</ossec_config>
"""


@pytest.mark.asyncio
async def test_upsert_block_adds_the_keyed_instance_and_restarts(db: AsyncSession) -> None:
    # THE virustotal case: no virustotal block exists (current_content "") — the
    # executor ADDS it, leaves the slack instance untouched, validates, restarts.
    proposal = await _proposal(
        db,
        action="upsert_block",
        parameters={"section_content": _VT_BLOCK, "current_content": ""},
        target={"section": "integration", "block_key": "virustotal"},
    )
    disk = _Disk(_OSSEC_INTEG)
    ctx = _ctx(db, disk)

    freshness, perform, verify = get_executor("config_change").build_forward(proposal, ctx)
    ok, reason = await freshness(proposal)
    assert ok is True
    assert "still absent" in reason

    res = await perform(proposal)
    assert len(ctx.action_api.writes) == 1
    assert ctx.action_api.restarts == 1
    assert "virustotal" in disk.content
    assert "hooks.example.invalid" in disk.content  # slack untouched
    assert proposal.prior_state is not None
    assert proposal.prior_state["content"] == _OSSEC_INTEG  # whole-file undo point
    assert res["section_updated"] is True
    assert res["block_key"] == "virustotal"

    ok, detail = await verify(proposal, res)
    assert ok is True
    assert detail["operation"] == "upsert_block"


@pytest.mark.asyncio
async def test_upsert_block_replaces_only_the_keyed_instance(db: AsyncSession) -> None:
    new_slack = (
        "<integration><name>slack</name>"
        "<hook_url>https://hooks.example.invalid/services/NEW</hook_url></integration>"
    )
    proposal = await _proposal(
        db,
        action="upsert_block",
        parameters={"section_content": new_slack, "current_content": _SLACK_BLOCK},
        target={"section": "integration", "block_key": "slack"},
    )
    disk = _Disk(_OSSEC_INTEG)
    ctx = _ctx(db, disk)
    freshness, perform, _v = get_executor("config_change").build_forward(proposal, ctx)
    assert (await freshness(proposal))[0] is True
    res = await perform(proposal)
    assert "services/NEW" in disk.content
    assert "services/T0" not in disk.content
    assert res["section_updated"] is True


@pytest.mark.asyncio
async def test_remove_block_removes_the_keyed_instance(db: AsyncSession) -> None:
    proposal = await _proposal(
        db,
        action="remove_block",
        parameters={"current_content": _SLACK_BLOCK},
        target={"section": "integration", "block_key": "slack"},
    )
    disk = _Disk(_OSSEC_INTEG)
    ctx = _ctx(db, disk)
    freshness, perform, verify = get_executor("config_change").build_forward(proposal, ctx)
    assert (await freshness(proposal))[0] is True
    res = await perform(proposal)
    assert "hooks.example.invalid" not in disk.content  # gone
    assert "<interval>12h</interval>" in disk.content  # sca untouched
    assert ctx.action_api.restarts == 1
    ok, detail = await verify(proposal, res)
    assert ok is True
    assert detail["operation"] == "remove_block"


@pytest.mark.asyncio
async def test_block_freshness_refuses_stale_or_appeared_targets(db: AsyncSession) -> None:
    # (a) an UPDATE whose keyed block drifted since propose → stale.
    drifted = _OSSEC_INTEG.replace("services/T0", "services/DRIFT")
    update = await _proposal(
        db,
        action="upsert_block",
        parameters={"section_content": _VT_BLOCK, "current_content": _SLACK_BLOCK},
        target={"section": "integration", "block_key": "slack"},
    )
    freshness, _p, _v = get_executor("config_change").build_forward(
        update, _ctx(db, _Disk(drifted))
    )
    ok, reason = await freshness(update)
    assert ok is False
    assert "changed since" in reason
    # (b) an ADD whose key has appeared since propose → stale (the config changed).
    added = _OSSEC_INTEG.replace("</ossec_config>", f"  {_VT_BLOCK}\n</ossec_config>")
    add = await _proposal(
        db,
        action="upsert_block",
        parameters={"section_content": _VT_BLOCK, "current_content": ""},
        target={"section": "integration", "block_key": "virustotal"},
    )
    freshness, _p, _v = get_executor("config_change").build_forward(add, _ctx(db, _Disk(added)))
    ok, reason = await freshness(add)
    assert ok is False
    assert "ADDS it" in reason


@pytest.mark.asyncio
async def test_remove_block_fails_honestly_if_removal_does_not_persist(db: AsyncSession) -> None:
    proposal = await _proposal(
        db,
        action="remove_block",
        parameters={"current_content": _SLACK_BLOCK},
        target={"section": "integration", "block_key": "slack"},
    )
    disk = _Disk(_OSSEC_INTEG)
    ctx = _ctx(db, disk, persist=False)  # writes "succeed" but don't stick
    _f, perform, _v = get_executor("config_change").build_forward(proposal, ctx)
    with pytest.raises(ConfigValidationError, match="did not persist"):
        await perform(proposal)
    assert ctx.action_api.restarts == 0


@pytest.mark.asyncio
async def test_reverse_restores_snapshot_hash_verifies_and_marks_completed(
    db: AsyncSession,
) -> None:
    original = await _proposal(
        db,
        action="update_section",
        parameters={"section_content": _SCA_NEW, "current_content": _SCA_CURRENT},
    )
    original.prior_state = {"kind": "manager_configuration", "content": _OSSEC, "sha256": "abc"}
    await db.flush()
    reversal = await _proposal(db, action="restore_config", parameters={}, reverses=original.id)

    # Disk currently holds the tuned config; restore should put the snapshot back.
    tuned = _OSSEC.replace(_SCA_CURRENT, _SCA_NEW)
    disk = _Disk(tuned)
    ctx = _ctx(db, disk)

    freshness, perform, verify = get_executor("config_change").build_reverse(reversal, ctx)
    assert (await freshness(reversal))[0] is True

    res = await perform(reversal)
    assert ctx.action_api.writes == [_OSSEC]  # the captured snapshot, restored
    assert ctx.action_api.restarts == 1
    assert res["config_restored"] is True

    ok, detail = await verify(reversal, res)
    assert ok is True
    assert detail["reversal_state"] == REVERSAL_STATE_COMPLETED


@pytest.mark.asyncio
async def test_reverse_freshness_refuses_when_already_rolled_back(db: AsyncSession) -> None:
    original = await _proposal(
        db,
        action="update_section",
        parameters={"section_content": _SCA_NEW, "current_content": _SCA_CURRENT},
    )
    original.prior_state = {"kind": "manager_configuration", "content": _OSSEC, "sha256": "abc"}
    original.state = ProposalState.rolled_back
    await db.flush()
    reversal = await _proposal(db, action="restore_config", parameters={}, reverses=original.id)
    ctx = _ctx(db, _Disk(_OSSEC))
    freshness, _p, _v = get_executor("config_change").build_reverse(reversal, ctx)
    ok, reason = await freshness(reversal)
    assert ok is False
    assert "already been reversed" in reason
