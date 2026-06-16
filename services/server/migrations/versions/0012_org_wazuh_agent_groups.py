"""Phase 6.6-c: per-org Wazuh agent-group scoping.

ADR 0020's per-org credential model adds an OPTIONAL ``wazuh_agent_groups``
field — the list of Wazuh agent groups an organization is scoped to (a
Wolf-side hint surfaced in the credentials UI + the probe scope summary;
Wazuh-side RBAC stays the authority).  Additive, nullable — no backfill
needed (null = "any group the credential can see").

The per-org Wazuh *URLs* are NOT changed here: they continue to live on the
row (sourced from the install-level ecosystem topology, 6.6-a, when the
credentials are saved via the new Superuser API) so the existing runtime
resolver keeps working unchanged until 6.6-e reads the topology fresh per
query and drops the now-redundant per-org URL columns.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organization_wazuh_configs",
        sa.Column("wazuh_agent_groups", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organization_wazuh_configs", "wazuh_agent_groups")
