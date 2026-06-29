"""ActionProposal ORM + proposal lifecycle states — Phase 6 (ADR 0025, doc 04).

A proposal is the typed, reviewable object a `propose_*` tool emits.  It changes
nothing itself; it is data placed in the approval queue.  Only a human approver
(separation of duties) advances it, and only the in-process gateway executes an
approved one.  Every transition is an append-only audit event.

The table is `organization_id`-scoped and forced-filtered exactly like the rest
of Wolf's per-org data (doc 05 / the cross-organization isolation gate).
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from wolf_server.database import Base

# JSONB on Postgres, generic JSON on SQLite (tests).  Same as audit/models.py.
_JSON_TYPE = JSONB().with_variant(JSON(), "sqlite")


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class ProposalState(enum.StrEnum):
    """The proposal lifecycle (doc 04 §The proposal lifecycle)."""

    draft = "draft"
    pending = "pending"
    approved = "approved"
    executing = "executing"
    succeeded = "succeeded"
    failed = "failed"
    rejected = "rejected"
    expired = "expired"
    rolled_back = "rolled_back"


# Terminal states never transition again (except succeeded → rolled_back).
TERMINAL_STATES = frozenset(
    {ProposalState.succeeded, ProposalState.failed, ProposalState.rejected, ProposalState.expired}
)


class ActionProposal(Base):
    """A reviewable, capability-bounded state-changing action awaiting approval.

    Immutable after creation EXCEPT for the lifecycle bookkeeping columns
    (``state`` + the approval/execution timestamps + ``result``).  The
    ``content_hash`` freezes the action's substance: a human approves *this
    hash*; the gateway executes *this hash*; any mismatch aborts.
    """

    __tablename__ = "action_proposals"
    __table_args__ = (
        Index("ix_action_proposals_org_state", "organization_id", "state"),
        Index("ix_action_proposals_org_created", "organization_id", "created_at"),
        # The timed auto-reversal sweep claims due, not-yet-reversed blocks.
        Index("ix_action_proposals_auto_unblock", "auto_unblock_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Immutable substance (covered by content_hash) ──────────────────────
    action_class: Mapped[str] = mapped_column(String(50), nullable=False)
    # Resolved, unambiguous target — agent_id + identifying detail for review.
    target: Mapped[dict[str, object]] = mapped_column(_JSON_TYPE, nullable=False)
    # The exact action (e.g. an active-response command id), never invented.
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    parameters: Mapped[dict[str, object]] = mapped_column(_JSON_TYPE, nullable=False, default=dict)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    # Alert / event ids the proposal is grounded in.
    evidence: Mapped[dict[str, object]] = mapped_column(_JSON_TYPE, nullable=False, default=dict)
    expected_effect: Mapped[str] = mapped_column(Text, nullable=False)
    rollback_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Computed (not model-chosen): "low" | "high" | "critical".
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # ── Reversal linkage (slice 6-d, ADR 0028) ─────────────────────────────
    # On a REVERSAL row: the block proposal it undoes (part of the content hash —
    # the approver approves *which* block is being reversed).
    reverses_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )

    # ── Lifecycle bookkeeping (mutable) ─────────────────────────────────────
    state: Mapped[str] = mapped_column(String(20), nullable=False, default=ProposalState.pending)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Verification-read result / failure detail (recorded at execute time).
    result: Mapped[dict[str, object] | None] = mapped_column(_JSON_TYPE, nullable=True)
    # On a timed BLOCK row: when its automatic reversal is due (set at
    # execution-success = executed_at + block_duration). NULL = no auto-reversal.
    auto_unblock_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # On a BLOCK row: the reversal proposal authorised for it (set when the
    # reversal is created). Prevents the sweep double-firing + drives the GUI;
    # the block flips to ``rolled_back`` only when wolf-pack confirms removal.
    reversal_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    # On a snapshot-restore forward action (rule_tuning / config_change, 6-e.3+):
    # the captured prior artifact (e.g. {"filename","relative_dirname","content",
    # "sha256"} for local_rules.xml) taken at execute time, BEFORE the write. The
    # reversal reads it from the original to PUT it back (a real undo). Mutable
    # bookkeeping like ``result`` — NOT part of content_hash.
    prior_state: Mapped[dict[str, object] | None] = mapped_column(_JSON_TYPE, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    # Short TTL — active response is time-sensitive (doc 04 §Stale proposals).
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<ActionProposal id={self.id} class={self.action_class!r} "
            f"state={self.state} org={self.organization_id}>"
        )
