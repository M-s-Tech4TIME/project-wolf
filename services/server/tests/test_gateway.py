"""In-process approval gateway — Phase 6 (ADR 0025, doc 04).

Covers the propose→approve→execute pipeline's safety properties end-to-end
against the test DB: the action validator (hard gate), content-hash freezing,
separation of duties, the state machine, freshness re-check, the verification
read, and fail-on-write-error.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_server.audit.models import AuditEvent
from wolf_server.gateway.approval import (
    ApprovalAuthorityError,
    ProposalExpiredError,
    SeparationOfDutiesError,
    approve_proposal,
    reject_proposal,
)
from wolf_server.gateway.execution import (
    ContentHashMismatchError,
    ProposalStaleError,
    execute_proposal,
)
from wolf_server.gateway.models import ActionProposal, ProposalState
from wolf_server.gateway.proposals import (
    compute_content_hash,
    create_proposal,
    is_expired,
    recompute_content_hash,
)
from wolf_server.gateway.state_machine import IllegalTransitionError, assert_transition
from wolf_server.gateway.validator import validate_proposal

_ORG = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_REQUESTER = uuid.UUID("11111111-1111-1111-1111-111111111111")
_APPROVER = uuid.UUID("22222222-2222-2222-2222-222222222222")


# ── Validator (hard gate) ──────────────────────────────────────────────────


def test_validator_accepts_resolved_low_risk_proposal() -> None:
    v = validate_proposal(
        action_class="active_response",
        target={"agent_id": "001"},
        action="firewall-drop",
        parameters={},
    )
    assert v.ok is True


def test_validator_rejects_unresolved_target() -> None:
    v = validate_proposal(
        action_class="active_response", target={}, action="firewall-drop", parameters={}
    )
    assert v.ok is False
    assert "resolved" in v.reason


@pytest.mark.parametrize("blast", ["*", "all"])
def test_validator_rejects_unbounded_blast_radius(blast: str) -> None:
    v = validate_proposal(
        action_class="active_response",
        target={"agent_id": "001"},
        action="firewall-drop",
        parameters={"agents_list": blast},
    )
    assert v.ok is False
    assert "blast radius" in v.reason


def test_validator_rejects_invented_command() -> None:
    v = validate_proposal(
        action_class="active_response",
        target={"agent_id": "001"},
        action="rm-rf-everything",
        parameters={},
    )
    assert v.ok is False
    assert "allow-list" in v.reason


def test_validator_rejects_unknown_action_class() -> None:
    v = validate_proposal(
        action_class="delete_universe", target={"agent_id": "1"}, action="x", parameters={}
    )
    assert v.ok is False


# ── Content hash ────────────────────────────────────────────────────────────


def test_content_hash_is_stable_and_substance_sensitive() -> None:
    base: dict[str, Any] = {
        "organization_id": _ORG,
        "action_class": "active_response",
        "target": {"agent_id": "001"},
        "action": "firewall-drop",
        "parameters": {},
        "evidence": {"alert_ids": ["a1"]},
        "expected_effect": "block",
        "rollback_plan": None,
        "severity": "low",
        "requested_by": _REQUESTER,
    }
    h1 = compute_content_hash(**base)
    assert h1 == compute_content_hash(**base)  # stable
    changed = {**base, "action": "host-deny"}
    assert compute_content_hash(**changed) != h1  # substance-sensitive


# ── State machine ───────────────────────────────────────────────────────────


def test_state_machine_blocks_illegal_edge() -> None:
    # pending cannot jump straight to executing.
    with pytest.raises(IllegalTransitionError):
        assert_transition(ProposalState.pending, ProposalState.executing)


def test_state_machine_allows_legal_edges() -> None:
    assert_transition(ProposalState.pending, ProposalState.approved)
    assert_transition(ProposalState.approved, ProposalState.executing)
    assert_transition(ProposalState.executing, ProposalState.succeeded)


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _make_pending(db: AsyncSession, **overrides: Any) -> ActionProposal:
    kwargs: dict[str, Any] = {
        "organization_id": _ORG,
        "requested_by": _REQUESTER,
        "action_class": "active_response",
        "target": {"agent_id": "001"},
        "action": "firewall-drop",
        "rationale": "brute force from this host",
        "expected_effect": "drop the offending IP for 10m",
        "evidence": {"alert_ids": ["a1", "a2"]},
    }
    kwargs.update(overrides)
    proposal = await create_proposal(db, **kwargs)
    await db.flush()
    return proposal


async def _fresh(_p: ActionProposal) -> tuple[bool, str]:
    return True, "agent present"


async def _stale(_p: ActionProposal) -> tuple[bool, str]:
    return False, "agent gone"


async def _perform_ok(_p: ActionProposal) -> dict[str, Any]:
    return {"data": {"total_affected_items": 1, "affected_items": ["001"]}}


async def _perform_raise(_p: ActionProposal) -> dict[str, Any]:
    raise RuntimeError("server api 500")


async def _verify_ok(_p: ActionProposal, res: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    return True, res["data"]


async def _verify_fail(_p: ActionProposal, _res: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    return False, {"reason": "not applied"}


# ── create_proposal ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_proposal_persists_pending_and_audits(db: AsyncSession) -> None:
    proposal = await _make_pending(db)
    assert proposal.state == ProposalState.pending
    assert proposal.severity == "low"
    assert recompute_content_hash(proposal) == proposal.content_hash
    # An audit event was written for the creation.
    events = (
        (await db.execute(select(AuditEvent).where(AuditEvent.organization_id == _ORG)))
        .scalars()
        .all()
    )
    assert any(e.event_type == "action.proposal.created" for e in events)


# ── Approval: separation of duties + authority + TTL ──────────────────────────


@pytest.mark.asyncio
async def test_requester_cannot_approve_own_proposal(db: AsyncSession) -> None:
    proposal = await _make_pending(db)
    with pytest.raises(SeparationOfDutiesError):
        await approve_proposal(
            db, proposal, approver_user_id=_REQUESTER, approver_role="responder"
        )
    assert proposal.state == ProposalState.pending  # unchanged


@pytest.mark.asyncio
async def test_role_without_approve_capability_is_refused(db: AsyncSession) -> None:
    proposal = await _make_pending(db)
    with pytest.raises(ApprovalAuthorityError):
        await approve_proposal(
            db, proposal, approver_user_id=_APPROVER, approver_role="analyst"
        )


@pytest.mark.asyncio
async def test_approve_advances_to_approved(db: AsyncSession) -> None:
    proposal = await _make_pending(db)
    await approve_proposal(db, proposal, approver_user_id=_APPROVER, approver_role="responder")
    assert proposal.state == ProposalState.approved
    assert proposal.approved_by == _APPROVER


@pytest.mark.asyncio
async def test_expired_proposal_cannot_be_approved(db: AsyncSession) -> None:
    proposal = await _make_pending(db)
    # Force expiry.
    proposal.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    assert is_expired(proposal) is True
    with pytest.raises(ProposalExpiredError):
        await approve_proposal(
            db, proposal, approver_user_id=_APPROVER, approver_role="admin"
        )
    assert proposal.state == ProposalState.expired


@pytest.mark.asyncio
async def test_reject_is_terminal(db: AsyncSession) -> None:
    proposal = await _make_pending(db)
    await reject_proposal(
        db, proposal, approver_user_id=_APPROVER, approver_role="responder", reason="false positive"
    )
    assert proposal.state == ProposalState.rejected


# ── Execution: freshness, write, verification ────────────────────────────────


@pytest.mark.asyncio
async def test_execute_success_path(db: AsyncSession) -> None:
    proposal = await _make_pending(db)
    await approve_proposal(db, proposal, approver_user_id=_APPROVER, approver_role="responder")
    await execute_proposal(
        db, proposal, freshness=_fresh, perform=_perform_ok, verify=_verify_ok,
        executor_user_id=_APPROVER,
    )
    assert proposal.state == ProposalState.succeeded
    assert proposal.executed_at is not None
    assert proposal.result == {"total_affected_items": 1, "affected_items": ["001"]}


@pytest.mark.asyncio
async def test_execute_refuses_when_stale(db: AsyncSession) -> None:
    proposal = await _make_pending(db)
    await approve_proposal(db, proposal, approver_user_id=_APPROVER, approver_role="responder")
    with pytest.raises(ProposalStaleError):
        await execute_proposal(
            db, proposal, freshness=_stale, perform=_perform_ok, verify=_verify_ok,
            executor_user_id=_APPROVER,
        )
    assert proposal.state == ProposalState.expired  # voided, not executed


@pytest.mark.asyncio
async def test_execute_refuses_on_content_hash_mismatch(db: AsyncSession) -> None:
    proposal = await _make_pending(db)
    await approve_proposal(db, proposal, approver_user_id=_APPROVER, approver_role="responder")
    # Tamper with the substance after approval — hash no longer matches.
    proposal.action = "host-deny"
    with pytest.raises(ContentHashMismatchError):
        await execute_proposal(
            db, proposal, freshness=_fresh, perform=_perform_ok, verify=_verify_ok,
            executor_user_id=_APPROVER,
        )


@pytest.mark.asyncio
async def test_execute_records_failed_on_write_error_no_retry(db: AsyncSession) -> None:
    proposal = await _make_pending(db)
    await approve_proposal(db, proposal, approver_user_id=_APPROVER, approver_role="responder")
    await execute_proposal(
        db, proposal, freshness=_fresh, perform=_perform_raise, verify=_verify_ok,
        executor_user_id=_APPROVER,
    )
    assert proposal.state == ProposalState.failed
    assert "error" in (proposal.result or {})


@pytest.mark.asyncio
async def test_execute_failed_when_verification_negative(db: AsyncSession) -> None:
    proposal = await _make_pending(db)
    await approve_proposal(db, proposal, approver_user_id=_APPROVER, approver_role="responder")
    await execute_proposal(
        db, proposal, freshness=_fresh, perform=_perform_ok, verify=_verify_fail,
        executor_user_id=_APPROVER,
    )
    assert proposal.state == ProposalState.failed
