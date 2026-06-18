"""Phase 6 (ADR 0025): action_proposals — the capability-driven approval queue.

A proposal is the typed, reviewable object a propose_* tool emits; a human
approves it; the in-process gateway executes the approved one against Wazuh via
the per-org credential.  Org-scoped + forced-filtered like all per-org data.

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "action_proposals"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action_class", sa.String(length=50), nullable=False),
        sa.Column("target", postgresql.JSONB(), nullable=False),
        sa.Column("action", sa.String(length=200), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("expected_effect", sa.Text(), nullable=False),
        sa.Column("rollback_plan", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("requested_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("approved_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_action_proposals_organization_id", _TABLE, ["organization_id"]
    )
    op.create_index(
        "ix_action_proposals_org_state", _TABLE, ["organization_id", "state"]
    )
    op.create_index(
        "ix_action_proposals_org_created", _TABLE, ["organization_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_action_proposals_org_created", table_name=_TABLE)
    op.drop_index("ix_action_proposals_org_state", table_name=_TABLE)
    op.drop_index("ix_action_proposals_organization_id", table_name=_TABLE)
    op.drop_table(_TABLE)
