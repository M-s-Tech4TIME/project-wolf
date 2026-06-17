"""Phase 6.6-f: per-org dynamic scoping by Wazuh ``agent.labels.group``.

ADR 0020's per-org credential model originally carried a static
``inject_organization_filter`` (inject ``term:{organization_id:<uuid>}`` into
every OpenSearch query) plus a ``wazuh_agent_groups`` UI hint.  Real-world RBAC
setup showed the static org-id filter is the wrong tool: Wazuh alerts never
carry ``organization_id``, and the per-org credential's own Wazuh RBAC + index
DLS is already the isolation boundary.  This migration replaces both columns:

  - ``inject_organization_filter`` → ``inject_group_label_filter`` (bool): when
    TRUE, Wolf injects ``terms:{agent.labels.group:[<labels>]}`` — the REAL
    Wazuh field — into every indexer query.  An opt-in belt-and-suspenders for
    credentials that are not themselves DLS-scoped.
  - ``wazuh_agent_groups`` → ``agent_group_labels`` (JSON list): the
    ``agent.labels.group`` values to inject.  Data is copied across.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "organization_wazuh_configs"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "inject_group_label_filter",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column("agent_group_labels", postgresql.JSONB(), nullable=True),
    )
    # Preserve the existing UI hint as the new injection label list.
    op.execute(
        "UPDATE organization_wazuh_configs SET agent_group_labels = wazuh_agent_groups"
    )
    op.drop_column(_TABLE, "inject_organization_filter")
    op.drop_column(_TABLE, "wazuh_agent_groups")


def downgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "inject_organization_filter",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column("wazuh_agent_groups", postgresql.JSONB(), nullable=True),
    )
    op.execute(
        "UPDATE organization_wazuh_configs SET wazuh_agent_groups = agent_group_labels"
    )
    op.drop_column(_TABLE, "inject_group_label_filter")
    op.drop_column(_TABLE, "agent_group_labels")
