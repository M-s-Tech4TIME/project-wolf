"""agent_action execution + API-executable reversal — Phase 6-e.2 (ADR 0029).

The agent_action executor performs the real group op via the bounded write client
and verifies via a fresh membership read; a reversal performs the inverse op for
real and (unlike AR's wolf-pack-bound reversal) flips the original to
``rolled_back`` via ``complete_api_reversal``.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_server.gateway.executors import ExecContext, get_executor
from wolf_server.gateway.models import ActionProposal, ProposalState
from wolf_server.gateway.proposals import create_proposal, create_reversal_proposal
from wolf_server.gateway.reversal import (
    REVERSAL_STATE_COMPLETED,
    REVERSAL_STATE_PENDING,
    complete_api_reversal,
)
from wolf_server.wazuh.capabilities import ACTION_MODIFY_GROUP, CredentialCapabilities

_ORG = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_REQUESTER = uuid.UUID("11111111-1111-1111-1111-111111111111")
_APPROVER = uuid.UUID("22222222-2222-2222-2222-222222222222")
_CAPS = CredentialCapabilities(policies={ACTION_MODIFY_GROUP: {"agent:id:*": "allow"}})


class _StubActionApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def assign_agent_group(
        self, *, agent_id: str, group: str, capabilities: Any, agent_groups: Any
    ) -> dict[str, Any]:
        self.calls.append(("assign", agent_id, group))
        return {"data": {"affected_items": [agent_id]}}

    async def remove_agent_group(
        self, *, agent_id: str, group: str, capabilities: Any, agent_groups: Any
    ) -> dict[str, Any]:
        self.calls.append(("remove", agent_id, group))
        return {"data": {"affected_items": [agent_id]}}


class _StubReadApi:
    """Returns a fixed group membership for /agents reads (freshness + verify)."""

    def __init__(self, groups: list[str]) -> None:
        self._groups = groups

    async def get(self, path: str, *, params: Any = None) -> dict[str, Any]:
        return {
            "data": {
                "affected_items": [{"id": "001", "group": self._groups}],
                "total_affected_items": 1,
            }
        }


async def _agent_action(
    db: AsyncSession, *, operation: str, group: str, state: ProposalState = ProposalState.succeeded
) -> ActionProposal:
    p = await create_proposal(
        db,
        organization_id=_ORG,
        requested_by=_REQUESTER,
        action_class="agent_action",
        target={"agent_id": "001"},
        action=operation,
        parameters={"group": group},
        rationale="quarantine",
        expected_effect="group change",
    )
    p.state = state
    p.executed_at = datetime.now(UTC)
    await db.flush()
    return p


@pytest.mark.asyncio
async def test_agent_action_forward_performs_and_verifies(db: AsyncSession) -> None:
    proposal = await _agent_action(db, operation="assign_group", group="isolated")
    action_api = _StubActionApi()
    # Post-assign membership includes 'isolated' → verify ok.
    ctx = ExecContext(
        read_api=_StubReadApi(["default", "isolated"]),
        action_api=action_api,
        capabilities=_CAPS,
        db=db,
    )
    _freshness, perform, verify = get_executor("agent_action").build_forward(proposal, ctx)
    res = await perform(proposal)
    assert action_api.calls == [("assign", "001", "isolated")]
    ok, detail = await verify(proposal, res)
    assert ok is True
    assert "isolated" in detail["agent_groups"]


@pytest.mark.asyncio
async def test_agent_action_reverse_marks_completed(db: AsyncSession) -> None:
    # A reversal proposal carries the inverse op (remove_group); its verify tags
    # the result completed so the original can flip to rolled_back.
    reversal = await _agent_action(db, operation="remove_group", group="isolated")
    reversal.reverses_proposal_id = uuid.uuid4()
    action_api = _StubActionApi()
    ctx = ExecContext(
        read_api=_StubReadApi(["default"]),  # 'isolated' gone → remove verified
        action_api=action_api,
        capabilities=_CAPS,
        db=db,
    )
    _freshness, perform, verify = get_executor("agent_action").build_reverse(reversal, ctx)
    res = await perform(reversal)
    assert action_api.calls == [("remove", "001", "isolated")]
    ok, detail = await verify(reversal, res)
    assert ok is True
    assert detail["reversal_state"] == REVERSAL_STATE_COMPLETED


@pytest.mark.asyncio
async def test_complete_api_reversal_flips_original_to_rolled_back(db: AsyncSession) -> None:
    block = await _agent_action(db, operation="assign_group", group="isolated")
    reversal = await create_reversal_proposal(
        db, block, requested_by=_REQUESTER, action="remove_group",
        parameters={"group": "isolated"}, rationale="undo", expected_effect="remove",
    )
    reversal.state = ProposalState.succeeded
    reversal.result = {"reversal_state": REVERSAL_STATE_COMPLETED}
    flipped = await complete_api_reversal(db, reversal, executor_user_id=_APPROVER)
    assert flipped is True
    await db.refresh(block)
    assert block.state == ProposalState.rolled_back


@pytest.mark.asyncio
async def test_complete_api_reversal_noop_when_wolf_pack_pending(db: AsyncSession) -> None:
    # An AR-style wolf-pack-bound reversal must NOT flip the original (it stays in
    # effect until wolf-pack confirms).
    block = await _agent_action(db, operation="assign_group", group="isolated")
    reversal = await create_reversal_proposal(
        db, block, requested_by=_REQUESTER, action="remove_group",
        parameters={"group": "isolated"}, rationale="undo", expected_effect="remove",
    )
    reversal.state = ProposalState.succeeded
    reversal.result = {"reversal_state": REVERSAL_STATE_PENDING}
    flipped = await complete_api_reversal(db, reversal, executor_user_id=_APPROVER)
    assert flipped is False
    await db.refresh(block)
    assert block.state == ProposalState.succeeded
