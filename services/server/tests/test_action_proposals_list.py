"""list action-proposals endpoint — state filter + ``state=all`` (Phase 6, 6-b).

The approval-queue GUI fetches the actionable queue (default ``pending``) and a
recent activity history (``state=all``).  Both are forced-filtered to the
caller's organization.  Exercised by calling the route function directly with a
hand-built context (mirrors test_propose_active_response driving the tool's
``run``).
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_server.api.action_proposals import list_proposals
from wolf_server.gateway.models import ProposalState
from wolf_server.gateway.proposals import create_proposal
from wolf_server.organization.context import OrganizationContext


def _ctx(org_id: uuid.UUID) -> OrganizationContext:
    return OrganizationContext(
        organization_id=org_id,
        organization_slug="acme",
        user_id=uuid.uuid4(),
        user_email="reviewer@example.com",
        role="responder",
        session_id="sess-1",
    )


async def _seed(db: AsyncSession, org_id: uuid.UUID, *, state: ProposalState) -> None:
    proposal = await create_proposal(
        db,
        organization_id=org_id,
        requested_by=uuid.uuid4(),
        action_class="active_response",
        target={"agent_id": "001"},
        action="firewall-drop",
        rationale="x",
        expected_effect="y",
        evidence={"alert_ids": []},
        session_id="sess-1",
    )
    if state is not ProposalState.pending:
        proposal.state = state
        await db.flush()


@pytest.mark.asyncio
async def test_list_default_returns_only_pending(db: AsyncSession) -> None:
    org = uuid.uuid4()
    await _seed(db, org, state=ProposalState.pending)
    await _seed(db, org, state=ProposalState.succeeded)
    rows = await list_proposals(_ctx(org), db)  # default = pending
    assert len(rows) == 1
    assert rows[0].state == ProposalState.pending.value


@pytest.mark.asyncio
async def test_list_all_returns_every_state(db: AsyncSession) -> None:
    org = uuid.uuid4()
    await _seed(db, org, state=ProposalState.pending)
    await _seed(db, org, state=ProposalState.succeeded)
    await _seed(db, org, state=ProposalState.rejected)
    rows = await list_proposals(_ctx(org), db, state="all")
    assert len(rows) == 3
    assert {r.state for r in rows} == {"pending", "succeeded", "rejected"}


@pytest.mark.asyncio
async def test_list_is_organization_scoped(db: AsyncSession) -> None:
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    await _seed(db, org_a, state=ProposalState.pending)
    await _seed(db, org_b, state=ProposalState.pending)
    rows = await list_proposals(_ctx(org_a), db, state="all")
    assert len(rows) == 1
