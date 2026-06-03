"""Add inject_tenant_filter to tenant_wazuh_configs.

Existing rows default to FALSE — vanilla Wazuh installs do not stamp a
`tenant_id` field on alerts, so the previous "always inject" behaviour
silently matched zero docs.  Operators with pooled-index multi-tenant
setups can flip the column to TRUE per tenant.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenant_wazuh_configs",
        sa.Column(
            "inject_tenant_filter",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_wazuh_configs", "inject_tenant_filter")
