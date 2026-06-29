"""rule_tuning execution + snapshot-restore reversal — Phase 6-e.3 (ADR 0029).

The rule_tuning executor writes an overwrite override into local_rules.xml and
APPLIES it (validate → cluster restart) with an auto-rollback if the edited
ruleset does not compile; the reversal restores the captured ``prior_state``
snapshot for real and tags the result completed so the original flips to
``rolled_back``.  Uses in-memory stubs for the Wazuh clients — no live manager.
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


class _StubReadApi:
    def __init__(self, *, level: int, validation_ok: bool = True) -> None:
        self.level = level
        self.validation_ok = validation_ok
        self.raw_reads: list[str] = []

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
        self.raw_reads.append(path)
        return _LOCAL_RULES


class _StubActionApi:
    def __init__(self) -> None:
        self.writes: list[str] = []
        self.restarts = 0

    async def update_rules_file(
        self, *, filename: str, content: str, capabilities: Any, relative_dirname: str = "etc/rules"
    ) -> dict[str, Any]:
        self.writes.append(content)
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
async def test_forward_writes_override_validates_and_restarts(db: AsyncSession) -> None:
    proposal = await _rule_proposal(db, action="disable_rule", level=0)
    read_api = _StubReadApi(level=0)  # post-change effective level
    action_api = _StubActionApi()
    ctx = ExecContext(read_api=read_api, action_api=action_api, capabilities=_CAPS, db=db)

    freshness, perform, verify = get_executor("rule_tuning").build_forward(proposal, ctx)
    fresh, _ = await freshness(proposal)
    assert fresh is True

    res = await perform(proposal)
    # Exactly one write (no rollback), one restart issued.
    assert len(action_api.writes) == 1
    assert action_api.restarts == 1
    written = action_api.writes[0]
    assert 'overwrite="yes"' in written
    assert 'level="0"' in written
    assert "<if_sid>5716</if_sid>" in written  # original matching preserved
    # prior_state captured for the undo.
    assert proposal.prior_state is not None
    assert proposal.prior_state["content"] == _LOCAL_RULES
    assert "sha256" in proposal.prior_state

    ok, detail = await verify(proposal, res)
    assert ok is True
    assert detail["matches"] is True
    assert detail["restart_issued"] is True


@pytest.mark.asyncio
async def test_forward_auto_rolls_back_on_validation_failure(db: AsyncSession) -> None:
    proposal = await _rule_proposal(db, action="adjust_level", level=2)
    read_api = _StubReadApi(level=2, validation_ok=False)
    action_api = _StubActionApi()
    ctx = ExecContext(read_api=read_api, action_api=action_api, capabilities=_CAPS, db=db)

    _freshness, perform, _verify = get_executor("rule_tuning").build_forward(proposal, ctx)
    with pytest.raises(RulesetValidationError):
        await perform(proposal)
    # The bad edit was written, then the prior file was restored — two writes, NO restart.
    assert len(action_api.writes) == 2
    assert action_api.writes[1] == _LOCAL_RULES  # restored snapshot
    assert action_api.restarts == 0


@pytest.mark.asyncio
async def test_reverse_restores_snapshot_and_marks_completed(db: AsyncSession) -> None:
    original = await _rule_proposal(db, action="disable_rule", level=0)
    original.prior_state = {
        "filename": "local_rules.xml",
        "relative_dirname": "etc/rules",
        "content": _LOCAL_RULES,
        "sha256": "abc",
    }
    await db.flush()
    reversal = await _rule_proposal(db, action="restore_rules", level=0, reverses=original.id)

    read_api = _StubReadApi(level=5)  # back to the original level
    action_api = _StubActionApi()
    ctx = ExecContext(read_api=read_api, action_api=action_api, capabilities=_CAPS, db=db)

    freshness, perform, verify = get_executor("rule_tuning").build_reverse(reversal, ctx)
    fresh, _ = await freshness(reversal)
    assert fresh is True

    res = await perform(reversal)
    assert action_api.writes == [_LOCAL_RULES]  # the captured snapshot, restored
    assert action_api.restarts == 1

    ok, detail = await verify(reversal, res)
    assert ok is True
    assert detail["reversal_state"] == REVERSAL_STATE_COMPLETED


@pytest.mark.asyncio
async def test_reverse_freshness_fails_without_prior_state(db: AsyncSession) -> None:
    original = await _rule_proposal(db, action="disable_rule", level=0)  # no prior_state set
    reversal = await _rule_proposal(db, action="restore_rules", level=0, reverses=original.id)
    ctx = ExecContext(
        read_api=_StubReadApi(level=5), action_api=_StubActionApi(), capabilities=_CAPS, db=db
    )
    freshness, _perform, _verify = get_executor("rule_tuning").build_reverse(reversal, ctx)
    fresh, detail = await freshness(reversal)
    assert fresh is False
    assert "prior_state" in detail.lower()
