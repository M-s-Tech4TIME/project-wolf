"""Phase 6.5-f: time-limited Superuser grants + access-request table.

ADR 0018's consent gate lets an org Admin grant the install Superuser
*time-limited* read/chat membership.  This migration adds the two
structures the flow needs:

  - ``user_organizations.expires_at`` (nullable): when non-null the
    membership auto-expires; it is pruned lazily at access time (no
    background scheduler — see organization/superuser_access.py).  Null
    means no expiry (every normal member, plus "until-revoked" grants).
  - ``superuser_access_requests``: the durable record of a Superuser's
    request for org membership + the Admin's approve/reject decision.
    The resulting grant itself lives on ``user_organizations``.

Constraint + index names are spelled out to match the model metadata's
NAMING_CONVENTION (wolf_server.database) so ``alembic check`` sees no
drift.

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_organizations",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "superuser_access_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("superuser_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("requested_duration_hours", sa.Integer(), nullable=True),
        sa.Column("granted_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_superuser_access_requests"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_superuser_access_requests_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["superuser_user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_superuser_access_requests_superuser_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name="fk_superuser_access_requests_decided_by_user_id_users",
        ),
    )
    op.create_index(
        "ix_superuser_access_requests_org_status",
        "superuser_access_requests",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_superuser_access_requests_superuser_user_id",
        "superuser_access_requests",
        ["superuser_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_superuser_access_requests_superuser_user_id",
        table_name="superuser_access_requests",
    )
    op.drop_index(
        "ix_superuser_access_requests_org_status",
        table_name="superuser_access_requests",
    )
    op.drop_table("superuser_access_requests")
    op.drop_column("user_organizations", "expires_at")
