"""Add tenant_wazuh_configs: per-tenant Wazuh connection profile.

The actual credentials (username/password) are NOT stored here — they live
in the secrets backend, keyed by `opensearch_credential_key` and
`server_api_credential_key`.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_wazuh_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("opensearch_url", sa.String(500), nullable=False),
        sa.Column(
            "opensearch_index_pattern",
            sa.String(200),
            nullable=False,
            server_default="wazuh-alerts-*",
        ),
        sa.Column("opensearch_credential_key", sa.String(200), nullable=False),
        sa.Column("server_api_url", sa.String(500), nullable=False),
        sa.Column("server_api_credential_key", sa.String(200), nullable=False),
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_wazuh_config_tenant"),
    )
    op.create_index(
        "ix_tenant_wazuh_configs_tenant_id", "tenant_wazuh_configs", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_wazuh_configs_tenant_id", table_name="tenant_wazuh_configs")
    op.drop_table("tenant_wazuh_configs")
