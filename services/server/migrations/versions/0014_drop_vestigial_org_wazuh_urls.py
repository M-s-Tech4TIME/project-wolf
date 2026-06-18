"""Phase 6.6-g: drop the vestigial per-org Wazuh URL/TLS columns.

Since 6.6-e the runtime resolver reads the indexer/manager URLs + TLS posture
from the install-level Wazuh ecosystem TOPOLOGY (``wazuh_ecosystem_topology``),
read fresh per query — NOT from ``organization_wazuh_configs``.  The per-org
``opensearch_url`` / ``server_api_url`` / ``verify_tls`` columns have been
written-but-never-read ever since (kept only to satisfy NOT-NULL).  This drops
them; the per-org row now holds only credential keys + index pattern + scoping.

Downgrade re-adds the columns (NOT NULL with placeholder server defaults — the
original URL values are not recoverable, which is fine: they were vestigial).

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "organization_wazuh_configs"


def upgrade() -> None:
    op.drop_column(_TABLE, "opensearch_url")
    op.drop_column(_TABLE, "server_api_url")
    op.drop_column(_TABLE, "verify_tls")


def downgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("opensearch_url", sa.String(length=500), nullable=False, server_default=""),
    )
    op.add_column(
        _TABLE,
        sa.Column("server_api_url", sa.String(length=500), nullable=False, server_default=""),
    )
    op.add_column(
        _TABLE,
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
