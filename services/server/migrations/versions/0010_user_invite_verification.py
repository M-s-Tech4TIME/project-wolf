"""Phase 6.5-h: invite-link verification (ADR 0018 item 9).

An Admin-created account now starts ``unverified`` and carries a
single-use invite token; the user pastes the invite link after logging
in to flip to ``verified`` (only verified users reach org data — the
gate lives in organization/context.py).  This migration adds the three
columns the flow needs to ``users``:

  - ``verification_status`` (NOT NULL): ``unverified`` / ``verified``,
    app-validated.  Added with a ``server_default`` of ``unverified`` so
    the column can be NOT NULL on existing rows; every pre-existing
    account is then backfilled to ``verified`` (they already have access
    — this migration must not lock anyone out, including the bootstrap
    Superuser).  The server_default is dropped afterwards so the model
    default ("unverified") governs newly created rows.
  - ``verification_token_hash`` (nullable): SHA-256 hex of the raw token,
    never the token itself; cleared when verification succeeds.
  - ``verification_token_expires_at`` (nullable): 7 days from generation.

No new constraints or indexes (lookups are by the already-authenticated
user's PK), so ``alembic check`` sees no drift.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "verification_status",
            sa.String(20),
            nullable=False,
            server_default="unverified",
        ),
    )
    op.add_column(
        "users",
        sa.Column("verification_token_hash", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "verification_token_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
    )

    # Pre-existing accounts already have access — grandfather them in so
    # the gate never locks out a current user (incl. the Superuser).
    op.execute("UPDATE users SET verification_status = 'verified'")

    # New rows are governed by the model default ("unverified"); the
    # server_default was only needed to make the column NOT NULL on the
    # existing table.
    op.alter_column("users", "verification_status", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "verification_token_expires_at")
    op.drop_column("users", "verification_token_hash")
    op.drop_column("users", "verification_status")
