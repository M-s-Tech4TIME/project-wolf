"""Phase 6.6-a: install-level Wazuh ecosystem topology.

ADR 0020 concentrates ALL Wazuh component configuration in the Superuser.
This migration adds the install-wide ecosystem topology table — where the
Wazuh indexer(s), manager(s) and dashboard physically live (one install =
one Wazuh ecosystem, ADR 0020 decision 4).  Distinct from the per-org
``organization_wazuh_configs`` table, which holds the credentials each org
uses to query that ecosystem (refactored in 6.6-c).

Single-row invariant: a unique constraint on the constant ``is_singleton``
flag guarantees at most one row.  The structural shape lives in the
``topology`` JSONB document (validated by wolf_server.wazuh.topology); the
install-level credentials live in the secrets backend, named by
``indexer_credential_key`` / ``manager_credential_key`` (ADR 0020 decision 7
— credentials never share a row with config metadata).

Constraint names are spelled out to match the model metadata's
NAMING_CONVENTION (wolf_server.database) so ``alembic check`` sees no drift.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wazuh_ecosystem_topology",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_singleton", sa.Boolean(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("topology", postgresql.JSONB(), nullable=False),
        sa.Column("indexer_credential_key", sa.String(200), nullable=False),
        sa.Column("manager_credential_key", sa.String(200), nullable=False),
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_wazuh_ecosystem_topology"),
        sa.UniqueConstraint("is_singleton", name="uq_wazuh_ecosystem_topology_singleton"),
    )


def downgrade() -> None:
    op.drop_table("wazuh_ecosystem_topology")
