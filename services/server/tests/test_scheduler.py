"""Timed auto-reversal scheduler — slice 6-d.3 (ADR 0028).

A due timed block is auto-reversed exactly once (idempotent on re-run), the
reversal is system-initiated + pre-consented (lands ``succeeded``, wolf-pack-
bound — no host touch), a manual early unblock pre-empts the sweep, and a
non-timed / not-yet-due block is never swept.

The sweep opens its own sessions via ``db_session()``, so these tests point that
at the test DB engine with ``override_engine`` (the same engine the ``db``
fixture uses) and seed rows through that engine so the sweep sees them.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine
from wolf_server.audit.models import AuditEvent
from wolf_server.database import db_session, override_engine
from wolf_server.gateway.models import ActionProposal, ProposalState
from wolf_server.gateway.proposals import create_reversal_proposal
from wolf_server.gateway.reversal import REVERSAL_STATE_PENDING
from wolf_server.gateway.scheduler import WOLF_SYSTEM_ACTOR, sweep_due_reversals

_ORG = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_REQUESTER = uuid.UUID("11111111-1111-1111-1111-111111111111")


async def _insert_timed_block(
    *,
    srcip: str = "203.0.113.7",
    command: str = "firewall-drop",
    auto_unblock_at: datetime | None,
    state: ProposalState = ProposalState.succeeded,
) -> uuid.UUID:
    """Insert a succeeded (timed) block via ``db_session`` so the sweep sees it."""
    now = datetime.now(UTC)
    async with db_session() as db:
        block = ActionProposal(
            organization_id=_ORG,
            action_class="active_response",
            target={"agent_id": "001"},
            action=command,
            parameters={"intent": "block_ip", "srcip": srcip, "agent_os": "Ubuntu 22.04"},
            rationale="brute-force auth (rule 5710)",
            evidence={"alert_ids": ["a1"]},
            expected_effect="block",
            rollback_plan=None,
            severity="high",
            requested_by=_REQUESTER,
            content_hash="0" * 64,
            state=state,
            executed_at=now,
            created_at=now,
            auto_unblock_at=auto_unblock_at,
            expires_at=now,
        )
        db.add(block)
        await db.flush()
        block_id = block.id
        await db.commit()
    return block_id


async def _load(block_id: uuid.UUID) -> ActionProposal:
    async with db_session() as db:
        return await db.get(ActionProposal, block_id)  # type: ignore[return-value]


@pytest_asyncio.fixture
async def _wired_engine(engine: AsyncEngine) -> AsyncGenerator[None]:
    """Point ``db_session()`` (which the sweep opens internally) at the test
    engine. The sweep COMMITS, and the ``engine`` fixture is session-scoped, so
    delete this org's committed rows on teardown to keep tests isolated."""
    with override_engine(engine):
        yield
        async with db_session() as db:
            await db.execute(delete(AuditEvent).where(AuditEvent.organization_id == _ORG))
            await db.execute(
                delete(ActionProposal).where(ActionProposal.organization_id == _ORG)
            )
            await db.commit()


@pytest.mark.asyncio
async def test_sweep_auto_reverses_a_due_timed_block(_wired_engine: Any) -> None:
    past = datetime.now(UTC) - timedelta(minutes=1)
    block_id = await _insert_timed_block(auto_unblock_at=past)

    processed = await sweep_due_reversals()
    assert processed == 1

    # The block is now stamped (won't re-fire) but still in effect (wolf-pack
    # removes it physically) — NOT rolled_back.
    block = await _load(block_id)
    assert block.reversal_proposal_id is not None
    assert block.state == ProposalState.succeeded

    reversal = await _load(block.reversal_proposal_id)
    assert reversal.reverses_proposal_id == block_id
    assert reversal.state == ProposalState.succeeded  # authorised + recorded
    assert reversal.requested_by == WOLF_SYSTEM_ACTOR
    assert reversal.parameters.get("auto") is True
    assert (reversal.result or {}).get("reversal_state") == REVERSAL_STATE_PENDING
    assert "expired" in reversal.rationale.lower()  # the auto-reversal context
    assert "brute-force auth" in reversal.rationale  # recalled original reason


@pytest.mark.asyncio
async def test_sweep_is_idempotent(_wired_engine: Any) -> None:
    past = datetime.now(UTC) - timedelta(minutes=1)
    await _insert_timed_block(auto_unblock_at=past)
    assert await sweep_due_reversals() == 1
    # A second run finds nothing due (the block is stamped) — no double reversal.
    assert await sweep_due_reversals() == 0


@pytest.mark.asyncio
async def test_sweep_ignores_not_yet_due_and_indefinite_blocks(_wired_engine: Any) -> None:
    future = datetime.now(UTC) + timedelta(hours=1)
    await _insert_timed_block(srcip="198.51.100.1", auto_unblock_at=future)  # not due
    await _insert_timed_block(srcip="198.51.100.2", auto_unblock_at=None)  # indefinite
    assert await sweep_due_reversals() == 0


@pytest.mark.asyncio
async def test_manual_unblock_preempts_the_sweep(_wired_engine: Any) -> None:
    """If a human reverses the block before the timer fires, the block is stamped
    and the sweep skips it (no double-fire)."""
    past = datetime.now(UTC) - timedelta(minutes=1)
    block_id = await _insert_timed_block(auto_unblock_at=past)
    async with db_session() as db:
        block = await db.get(ActionProposal, block_id)
        assert block is not None
        await create_reversal_proposal(
            db, block, requested_by=_REQUESTER, action="firewall-drop",
            parameters={"intent": "unblock_ip", "reversal": True, "srcip": "203.0.113.7"},
            rationale="manual undo", expected_effect="unblock",
        )
        await db.commit()
    assert await sweep_due_reversals() == 0
