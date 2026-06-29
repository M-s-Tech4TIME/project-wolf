"""Slice 6-e.3 (ADR 0029): snapshot-restore prior_state for API-executable reversal.

Additive, nullable column on ``action_proposals`` — no backfill:
  - ``prior_state`` — on a snapshot-restore forward action (rule_tuning, and
    later config_change), the captured prior artifact (e.g. the ``local_rules.xml``
    content + hash) taken at execute time, BEFORE the write.  The reversal reads
    it from the original proposal and PUTs it back, performing a real undo.

Execute-time bookkeeping like ``result`` — NOT part of ``content_hash`` (it is
captured after approval, during the bounded write).

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "action_proposals"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("prior_state", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, "prior_state")
