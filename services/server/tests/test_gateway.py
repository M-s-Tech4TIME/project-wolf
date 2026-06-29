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
    compute_severity,
    create_proposal,
    create_reversal_proposal,
    find_active_action,
    find_active_block,
    is_expired,
    list_active_blocks,
    recompute_content_hash,
    stamp_auto_unblock_at,
)
from wolf_server.gateway.reversal import (
    REVERSAL_STATE_PENDING,
    is_reversal,
    reversal_freshness,
    reversal_perform,
    reversal_verify,
)
from wolf_server.gateway.state_machine import IllegalTransitionError, assert_transition
from wolf_server.gateway.validator import validate_proposal

_ORG = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_ORG2 = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
_REQUESTER = uuid.UUID("11111111-1111-1111-1111-111111111111")
_APPROVER = uuid.UUID("22222222-2222-2222-2222-222222222222")


# ── Validator (hard gate) ──────────────────────────────────────────────────


def test_validator_accepts_resolved_low_risk_proposal() -> None:
    v = validate_proposal(
        action_class="active_response",
        target={"agent_id": "001"},
        action="firewall-drop",
        parameters={"srcip": "203.0.113.7"},
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
    assert "catalog" in v.reason


def test_validator_rejects_unknown_action_class() -> None:
    v = validate_proposal(
        action_class="delete_universe", target={"agent_id": "1"}, action="x", parameters={}
    )
    assert v.ok is False


def test_validator_requires_srcip_for_block_command() -> None:
    v = validate_proposal(
        action_class="active_response",
        target={"agent_id": "001"},
        action="firewall-drop",
        parameters={},  # no srcip
    )
    assert v.ok is False
    assert "srcip" in v.reason


def test_validator_rejects_malformed_srcip() -> None:
    v = validate_proposal(
        action_class="active_response",
        target={"agent_id": "001"},
        action="firewall-drop",
        parameters={"srcip": "not.an.ip"},
    )
    assert v.ok is False
    assert "valid IP" in v.reason


def test_validator_requires_username_for_disable_account() -> None:
    v = validate_proposal(
        action_class="active_response",
        target={"agent_id": "001"},
        action="disable-account",
        parameters={},  # no username
    )
    assert v.ok is False
    assert "username" in v.reason


def test_validator_accepts_restart_wazuh_without_target() -> None:
    v = validate_proposal(
        action_class="active_response",
        target={"agent_id": "001"},
        action="restart-wazuh",
        parameters={},
    )
    assert v.ok is True


def test_validator_rejects_platform_mismatch() -> None:
    # firewall-drop is Linux-only; a clearly-Windows agent must be refused.
    v = validate_proposal(
        action_class="active_response",
        target={"agent_id": "003"},
        action="firewall-drop",
        parameters={"srcip": "203.0.113.7", "agent_os": "Microsoft Windows Server 2019"},
    )
    assert v.ok is False
    assert "windows" in v.reason.lower()


def test_validator_lenient_on_unknown_platform() -> None:
    # An unresolved/unknown OS must NOT block (the 6-a.1 lesson: no false refusals).
    v = validate_proposal(
        action_class="active_response",
        target={"agent_id": "001"},
        action="firewall-drop",
        parameters={"srcip": "203.0.113.7", "agent_os": "some-appliance-os"},
    )
    assert v.ok is True


def test_validator_accepts_netsh_on_windows() -> None:
    v = validate_proposal(
        action_class="active_response",
        target={"agent_id": "003"},
        action="netsh",
        parameters={"srcip": "203.0.113.7", "agent_os": "Microsoft Windows Server 2019"},
    )
    assert v.ok is True


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


# ── Dynamic severity (catalog base + context escalation) ─────────────────────


def test_severity_is_catalog_driven_and_dynamic() -> None:
    # Base impact comes from the command's catalog severity.
    assert compute_severity("active_response", "firewall-drop", {}) == "high"  # block IP
    assert compute_severity("active_response", "netsh", {}) == "high"  # block IP (Windows)
    assert compute_severity("active_response", "pf", {}) == "high"  # block IP (BSD)
    assert compute_severity("active_response", "restart-wazuh", {}) == "low"  # restart
    assert (
        compute_severity("active_response", "disable-account", {"username": "jdoe"}) == "medium"
    )  # disable an ordinary user
    # Context escalation: disabling a privileged account is high, not medium.
    assert (
        compute_severity("active_response", "disable-account", {"username": "root"}) == "high"
    )
    assert (
        compute_severity("active_response", "disable-account", {"username": "Administrator"})
        == "high"
    )
    # Unknown command / unregistered class falls back to low (never crashes).
    assert compute_severity("active_response", "made-up", {}) == "low"
    assert compute_severity("not_a_registered_class", "anything", {}) == "low"


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
    assert proposal.severity == "high"  # firewall-drop = block IP = high (6-c.1)
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


# ── reversal ledger + execution (slice 6-d, ADR 0028) ────────────────────────


async def _succeeded_block(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID = _ORG,
    srcip: str = "203.0.113.7",
    command: str = "firewall-drop",
    duration_seconds: int | None = None,
) -> ActionProposal:
    params: dict[str, Any] = {"intent": "block_ip", "srcip": srcip}
    if duration_seconds is not None:
        params["block_duration_seconds"] = duration_seconds
    block = await _make_pending(
        db, organization_id=organization_id, action=command, parameters=params
    )
    block.state = ProposalState.succeeded
    block.executed_at = datetime.now(UTC)
    await db.flush()
    return block


@pytest.mark.asyncio
async def test_find_active_block_matches_target_and_org(db: AsyncSession) -> None:
    block = await _succeeded_block(db, srcip="203.0.113.7")
    found = await find_active_block(
        db, organization_id=_ORG, action_class="active_response",
        agent_id="001", srcip="203.0.113.7",
    )
    assert found is not None and found.id == block.id
    # Different IP → no match.
    assert await find_active_block(
        db, organization_id=_ORG, action_class="active_response",
        agent_id="001", srcip="8.8.8.8",
    ) is None
    # Another org cannot see this org's block (cross-organization isolation).
    assert await find_active_block(
        db, organization_id=_ORG2, action_class="active_response",
        agent_id="001", srcip="203.0.113.7",
    ) is None


@pytest.mark.asyncio
async def test_create_reversal_links_block_and_stamps_it(db: AsyncSession) -> None:
    block = await _succeeded_block(db, srcip="203.0.113.7")
    reversal = await create_reversal_proposal(
        db, block, requested_by=_REQUESTER, action="firewall-drop",
        parameters={"intent": "unblock_ip", "reversal": True, "srcip": "203.0.113.7"},
        rationale="undo", expected_effect="unblock",
    )
    assert reversal.reverses_proposal_id == block.id
    assert block.reversal_proposal_id == reversal.id
    assert is_reversal(reversal) is True and is_reversal(block) is False
    # A stamped block is no longer "active" for recall / the sweep.
    assert await find_active_block(
        db, organization_id=_ORG, action_class="active_response",
        agent_id="001", srcip="203.0.113.7",
    ) is None


@pytest.mark.asyncio
async def test_list_active_blocks_excludes_reversed(db: AsyncSession) -> None:
    b1 = await _succeeded_block(db, srcip="203.0.113.7")
    await _succeeded_block(db, srcip="198.51.100.9")
    blocks = await list_active_blocks(db, organization_id=_ORG)
    assert {b.parameters.get("srcip") for b in blocks} == {"203.0.113.7", "198.51.100.9"}
    # Reverse one → it drops out of the ledger.
    await create_reversal_proposal(
        db, b1, requested_by=_REQUESTER, action="firewall-drop",
        parameters={"intent": "unblock_ip", "reversal": True, "srcip": "203.0.113.7"},
        rationale="undo", expected_effect="unblock",
    )
    blocks = await list_active_blocks(db, organization_id=_ORG)
    assert {b.parameters.get("srcip") for b in blocks} == {"198.51.100.9"}


@pytest.mark.asyncio
async def test_reversal_execute_records_deferral_and_block_stays_in_effect(
    db: AsyncSession,
) -> None:
    """Option A: an approved reversal lands ``succeeded`` (authorised + recorded)
    but does NOT touch the host (dispatched False, wolf-pack-bound) and the block
    is NOT rolled_back until wolf-pack confirms removal."""
    block = await _succeeded_block(db, srcip="203.0.113.7")
    reversal = await create_reversal_proposal(
        db, block, requested_by=_REQUESTER, action="firewall-drop",
        parameters={"intent": "unblock_ip", "reversal": True, "srcip": "203.0.113.7"},
        rationale="undo", expected_effect="unblock",
    )
    await approve_proposal(db, reversal, approver_user_id=_APPROVER, approver_role="responder")

    async def _rev_fresh(p: ActionProposal) -> tuple[bool, str]:
        return await reversal_freshness(db, p)

    await execute_proposal(
        db, reversal, freshness=_rev_fresh, perform=reversal_perform, verify=reversal_verify,
        executor_user_id=_APPROVER,
    )
    assert reversal.state == ProposalState.succeeded
    assert (reversal.result or {}).get("dispatched") is False
    assert (reversal.result or {}).get("reversal_state") == REVERSAL_STATE_PENDING
    assert (reversal.result or {}).get("deferred_to") == "wolf-pack"
    # The block is still in effect — physical removal is wolf-pack's job.
    assert block.state == ProposalState.succeeded


@pytest.mark.asyncio
async def test_stamp_auto_unblock_at_from_duration(db: AsyncSession) -> None:
    timed = await _succeeded_block(db, srcip="203.0.113.7", duration_seconds=3600)
    stamp_auto_unblock_at(timed)
    assert timed.auto_unblock_at is not None
    delta = timed.auto_unblock_at - (timed.executed_at or datetime.now(UTC))
    assert abs(delta - timedelta(seconds=3600)) < timedelta(seconds=2)
    # An indefinite block (no duration) gets no auto-reversal time.
    indefinite = await _succeeded_block(db, srcip="198.51.100.9")
    stamp_auto_unblock_at(indefinite)
    assert indefinite.auto_unblock_at is None


# ── per-class registry / executor dispatch (slice 6-e.1, ADR 0029) ───────────


def test_get_executor_returns_active_response_executor() -> None:
    from wolf_server.gateway.executors import get_executor

    ex = get_executor("active_response")
    assert hasattr(ex, "build_forward") and hasattr(ex, "build_reverse")


def test_get_executor_unknown_class_raises() -> None:
    from wolf_server.gateway.executors import UnknownActionClassError, get_executor

    with pytest.raises(UnknownActionClassError):
        get_executor("no_such_class")


def test_validate_proposal_rejects_unregistered_class() -> None:
    # The dispatch refuses any class without a registered validator (no invented
    # action classes reach the queue).
    v = validate_proposal(
        action_class="totally_made_up", target={"agent_id": "001"}, action="x", parameters={}
    )
    assert v.ok is False
    assert "unknown action class" in v.reason.lower()


def test_compute_severity_unregistered_class_is_low() -> None:
    assert compute_severity("totally_made_up", "x", {}) == "low"


@pytest.mark.asyncio
async def test_find_active_action_matches_with_custom_matcher(db: AsyncSession) -> None:
    # The generalized finder returns the most-recent succeeded, unreversed action
    # of the class that satisfies an arbitrary matcher (agent_action/rule_tuning
    # use their own matchers; this proves the generalization).
    block = await _succeeded_block(db, srcip="203.0.113.7")
    hit = await find_active_action(
        db,
        organization_id=_ORG,
        action_class="active_response",
        matcher=lambda p: str(p.target.get("agent_id", "")) == "001",
    )
    assert hit is not None and hit.id == block.id
    miss = await find_active_action(
        db, organization_id=_ORG, action_class="active_response", matcher=lambda p: False
    )
    assert miss is None
