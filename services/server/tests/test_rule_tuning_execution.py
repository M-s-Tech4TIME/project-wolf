"""rule_tuning execution + snapshot-restore reversal — Phase 6-e.3 (ADR 0029).

The rule_tuning executor writes an overwrite override into local_rules.xml and
APPLIES it (validate → cluster restart) with an auto-rollback if the edited
ruleset does not compile.  It then AUTHORITATIVELY confirms the override actually
persisted on disk (``GET`` reflects the on-disk file immediately) before declaring
success — a phantom no-op fails honestly.  The reversal restores the captured
``prior_state`` snapshot and confirms the override is gone.  In-memory stubs share
a ``_Disk`` so a read-back reflects what was written — no live manager.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_server.gateway.executors import (
    ExecContext,
    RulesetValidationError,
    get_executor,
)
from wolf_server.gateway.models import ActionProposal, ProposalState
from wolf_server.gateway.proposals import create_proposal
from wolf_server.gateway.reversal import REVERSAL_STATE_COMPLETED
from wolf_server.wazuh.capabilities import (
    ACTION_CLUSTER_RESTART,
    ACTION_UPDATE_RULES,
    CredentialCapabilities,
)

_ORG = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_REQUESTER = uuid.UUID("11111111-1111-1111-1111-111111111111")
_CAPS = CredentialCapabilities(
    policies={
        ACTION_UPDATE_RULES: {"*:*:*": "allow"},
        ACTION_CLUSTER_RESTART: {"*:*:*": "allow"},
    }
)

_LOCAL_RULES = """<!-- Local rules -->
<group name="sshd,">
  <rule id="100001" level="5">
    <if_sid>5716</if_sid>
    <description>sshd: auth failed.</description>
  </rule>
</group>
"""


class _Disk:
    """Shared on-disk state: get_raw reflects whatever update_rules_file last wrote
    (mirrors Wazuh — GET /rules/files reads the on-disk file immediately)."""

    def __init__(self, content: str) -> None:
        self.content = content


class _StubReadApi:
    def __init__(self, disk: _Disk, *, level: int, validation_ok: bool = True) -> None:
        self.disk = disk
        self.level = level
        self.validation_ok = validation_ok

    async def get(self, path: str, *, params: Any = None) -> dict[str, Any]:
        if path == "/rules":
            return {
                "data": {
                    "affected_items": [
                        {
                            "id": int(str(params["rule_ids"])),
                            "level": self.level,
                            "filename": "local_rules.xml",
                            "relative_dirname": "etc/rules",
                            "status": "enabled",
                        }
                    ],
                    "total_affected_items": 1,
                }
            }
        if path == "/manager/configuration/validation":
            if self.validation_ok:
                return {"data": {"affected_items": [{"name": "m", "status": "OK"}],
                                 "total_failed_items": 0}}
            return {"data": {"affected_items": [{"name": "m", "status": "ERROR"}],
                             "total_failed_items": 1, "failed_items": [{"error": "bad xml"}]}}
        return {"data": {}}

    async def get_raw(self, path: str, *, params: Any = None) -> str:
        return self.disk.content


class _StubActionApi:
    def __init__(self, disk: _Disk, *, persist: bool = True) -> None:
        self.disk = disk
        self.persist = persist  # False simulates a write that silently doesn't stick
        self.writes: list[str] = []
        self.restarts = 0

    async def update_rules_file(
        self, *, filename: str, content: str, capabilities: Any, relative_dirname: str = "etc/rules"
    ) -> dict[str, Any]:
        self.writes.append(content)
        if self.persist:
            self.disk.content = content
        return {"data": {"affected_items": [filename]}}

    async def restart_cluster(self, *, capabilities: Any) -> dict[str, Any]:
        self.restarts += 1
        return {"data": {"affected_items": ["m"]}}


async def _rule_proposal(
    db: AsyncSession, *, action: str, level: int, reverses: uuid.UUID | None = None
) -> ActionProposal:
    p = await create_proposal(
        db,
        organization_id=_ORG,
        requested_by=_REQUESTER,
        action_class="rule_tuning",
        target={"rule_id": "100001"},
        action=action,
        parameters={"level": level},
        rationale="noisy rule",
        expected_effect="tune",
        reverses_proposal_id=reverses,
    )
    p.state = ProposalState.succeeded
    p.executed_at = datetime.now(UTC)
    await db.flush()
    return p


@pytest.mark.asyncio
async def test_forward_writes_override_validates_confirms_and_restarts(db: AsyncSession) -> None:
    proposal = await _rule_proposal(db, action="disable_rule", level=0)
    disk = _Disk(_LOCAL_RULES)
    read_api = _StubReadApi(disk, level=0)  # post-override the local entry reads level 0
    action_api = _StubActionApi(disk)
    ctx = ExecContext(read_api=read_api, action_api=action_api, capabilities=_CAPS, db=db)

    freshness, perform, verify = get_executor("rule_tuning").build_forward(proposal, ctx)
    assert (await freshness(proposal))[0] is True

    res = await perform(proposal)
    # Exactly one write (no rollback), one restart issued.
    assert len(action_api.writes) == 1
    assert action_api.restarts == 1
    written = action_api.writes[0]
    assert 'overwrite="yes"' in written
    assert 'level="0"' in written
    assert "<if_sid>5716</if_sid>" in written  # original matching preserved
    assert "wolf-tuning:rule=100001" in written  # our marked override block
    # prior_state captured for the undo.
    assert proposal.prior_state is not None
    assert proposal.prior_state["content"] == _LOCAL_RULES
    # Authoritative confirmation surfaced in the result.
    assert res["override_written"] is True
    assert res["target_level_in_ruleset"] is True

    ok, detail = await verify(proposal, res)
    assert ok is True
    assert detail["override_written"] is True
    assert detail["target_level_in_ruleset"] is True
    assert detail["restart_issued"] is True


@pytest.mark.asyncio
async def test_forward_auto_rolls_back_on_validation_failure(db: AsyncSession) -> None:
    proposal = await _rule_proposal(db, action="adjust_level", level=2)
    disk = _Disk(_LOCAL_RULES)
    read_api = _StubReadApi(disk, level=2, validation_ok=False)
    action_api = _StubActionApi(disk)
    ctx = ExecContext(read_api=read_api, action_api=action_api, capabilities=_CAPS, db=db)

    _freshness, perform, _verify = get_executor("rule_tuning").build_forward(proposal, ctx)
    with pytest.raises(RulesetValidationError):
        await perform(proposal)
    # The bad edit was written, then the prior file restored — two writes, NO restart.
    assert len(action_api.writes) == 2
    assert action_api.writes[1] == _LOCAL_RULES
    assert action_api.restarts == 0


@pytest.mark.asyncio
async def test_forward_fails_honestly_if_override_does_not_persist(db: AsyncSession) -> None:
    # The exact bug: if the write doesn't actually land (override absent on re-read),
    # the action must FAIL + restore — never report a phantom success.
    proposal = await _rule_proposal(db, action="disable_rule", level=0)
    disk = _Disk(_LOCAL_RULES)
    read_api = _StubReadApi(disk, level=5)
    action_api = _StubActionApi(disk, persist=False)  # writes "succeed" but don't stick
    ctx = ExecContext(read_api=read_api, action_api=action_api, capabilities=_CAPS, db=db)

    _freshness, perform, _verify = get_executor("rule_tuning").build_forward(proposal, ctx)
    with pytest.raises(RulesetValidationError, match="did not persist"):
        await perform(proposal)
    assert action_api.restarts == 0  # never applied a phantom change


@pytest.mark.asyncio
async def test_reverse_restores_snapshot_confirms_removed_and_marks_completed(
    db: AsyncSession,
) -> None:
    original = await _rule_proposal(db, action="disable_rule", level=0)
    original.prior_state = {
        "filename": "local_rules.xml",
        "relative_dirname": "etc/rules",
        "content": _LOCAL_RULES,
        "sha256": "abc",
    }
    await db.flush()
    reversal = await _rule_proposal(db, action="restore_rules", level=0, reverses=original.id)

    # Disk currently holds a tuned file (override present); restore should remove it.
    disk = _Disk(_LOCAL_RULES + '\n<!-- wolf-tuning:rule=100001 -->\n<group></group>\n')
    read_api = _StubReadApi(disk, level=5)
    action_api = _StubActionApi(disk)
    ctx = ExecContext(read_api=read_api, action_api=action_api, capabilities=_CAPS, db=db)

    freshness, perform, verify = get_executor("rule_tuning").build_reverse(reversal, ctx)
    assert (await freshness(reversal))[0] is True

    res = await perform(reversal)
    assert action_api.writes == [_LOCAL_RULES]  # the captured snapshot, restored
    assert action_api.restarts == 1
    assert res["override_removed"] is True

    ok, detail = await verify(reversal, res)
    assert ok is True
    assert detail["reversal_state"] == REVERSAL_STATE_COMPLETED


@pytest.mark.asyncio
async def test_reverse_freshness_fails_without_prior_state(db: AsyncSession) -> None:
    original = await _rule_proposal(db, action="disable_rule", level=0)  # no prior_state set
    reversal = await _rule_proposal(db, action="restore_rules", level=0, reverses=original.id)
    disk = _Disk(_LOCAL_RULES)
    ctx = ExecContext(
        read_api=_StubReadApi(disk, level=5), action_api=_StubActionApi(disk),
        capabilities=_CAPS, db=db,
    )
    freshness, _perform, _verify = get_executor("rule_tuning").build_reverse(reversal, ctx)
    fresh, detail = await freshness(reversal)
    assert fresh is False
    assert "prior_state" in detail.lower()
