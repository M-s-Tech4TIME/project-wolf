"""Slice 6-d (ADR 0028): action-proposal reversal linkage + timed auto-reversal.

Additive, nullable columns on ``action_proposals`` — no backfill:
  - ``reverses_proposal_id`` — on a reversal row, the block it undoes.
  - ``auto_unblock_at``      — on a timed block, when its auto-reversal is due.
  - ``reversal_proposal_id`` — on a block, the reversal authorised for it.

Plus the index the timed auto-reversal sweep uses to claim due blocks.

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "action_proposals"
_AUTO_UNBLOCK_INDEX = "ix_action_proposals_auto_unblock"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("reverses_proposal_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column("auto_unblock_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column("reversal_proposal_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_index(_AUTO_UNBLOCK_INDEX, _TABLE, ["auto_unblock_at"])


def downgrade() -> None:
    op.drop_index(_AUTO_UNBLOCK_INDEX, table_name=_TABLE)
    op.drop_column(_TABLE, "reversal_proposal_id")
    op.drop_column(_TABLE, "auto_unblock_at")
    op.drop_column(_TABLE, "reverses_proposal_id")
