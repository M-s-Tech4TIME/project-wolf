"""Rename tenant → organization across the schema (Phase 6.4).

Per ADR 0018 (Bootstrap Superuser + Per-Org RBAC + Login UX) §"Implementation
sequencing" and the standing rule [[tenant-renamed-to-organization]]: Wolf's
canonical naming is "organization" from 2026-06-10 forward. This migration
renames every schema object — tables, columns, named constraints, indexes,
and the Postgres-auto-named FK constraints — to the new naming.

Wolf is pre-production at the time of this migration, so no operator
deployment has accumulated state on the legacy names; the rename is purely
mechanical. The migration is reversible via `downgrade()` for symmetry, even
though we don't expect anyone to actually run it.

Renames performed:

  tables:
    tenants                 → organizations
    user_tenants            → user_organizations
    tenant_wazuh_configs    → organization_wazuh_configs

  columns:
    user_tenants.tenant_id              → user_organizations.organization_id
    audit_events.tenant_id              → audit_events.organization_id
    tenant_wazuh_configs.tenant_id      → organization_wazuh_configs.organization_id
    tenant_wazuh_configs.inject_tenant_filter
                                        → organization_wazuh_configs.inject_organization_filter
    knowledge_chunks.tenant_id          → knowledge_chunks.organization_id

  named unique constraints:
    uq_tenants_slug                     → uq_organizations_slug
    uq_user_tenant                      → uq_user_organization
    uq_tenant_wazuh_config_tenant       → uq_organization_wazuh_config_organization

  FK constraints (Postgres-auto-named — renamed for naming consistency):
    user_tenants_tenant_id_fkey         → user_organizations_organization_id_fkey
    tenant_wazuh_configs_tenant_id_fkey → organization_wazuh_configs_organization_id_fkey
    (user_tenants_user_id_fkey         → user_organizations_user_id_fkey — table rename only)

  indexes:
    ix_tenants_slug                     → ix_organizations_slug
    ix_user_tenants_user_id             → ix_user_organizations_user_id
    ix_user_tenants_tenant_id           → ix_user_organizations_organization_id
    ix_audit_events_tenant_created      → ix_audit_events_organization_created
    ix_tenant_wazuh_configs_tenant_id   → ix_organization_wazuh_configs_organization_id
    ix_knowledge_chunks_tenant_source   → ix_knowledge_chunks_organization_source
    ix_knowledge_chunks_tenant_id       → ix_knowledge_chunks_organization_id

The migration uses Postgres-native ALTER ... RENAME ... statements so
indexes and constraints are renamed in place without rebuild — fast and
preserves data integrity even on future tables that accumulate rows.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── tables ────────────────────────────────────────────────────────────────
    # Order: rename the parent table (tenants) FIRST. FK definitions stored in
    # pg_constraint reference the table OID, so they survive the rename
    # automatically; only the constraint *name* lags. We rename constraint
    # names explicitly below for naming consistency.
    op.rename_table("tenants", "organizations")
    op.rename_table("user_tenants", "user_organizations")
    op.rename_table("tenant_wazuh_configs", "organization_wazuh_configs")

    # ── columns ───────────────────────────────────────────────────────────────
    op.alter_column("user_organizations", "tenant_id", new_column_name="organization_id")
    op.alter_column("audit_events", "tenant_id", new_column_name="organization_id")
    op.alter_column(
        "organization_wazuh_configs",
        "tenant_id",
        new_column_name="organization_id",
    )
    op.alter_column(
        "organization_wazuh_configs",
        "inject_tenant_filter",
        new_column_name="inject_organization_filter",
    )
    op.alter_column("knowledge_chunks", "tenant_id", new_column_name="organization_id")

    # ── named unique constraints ──────────────────────────────────────────────
    # Postgres-native ALTER ... RENAME CONSTRAINT preserves the constraint OID
    # so dependent rows are not re-validated.
    op.execute(
        "ALTER TABLE organizations RENAME CONSTRAINT uq_tenants_slug TO uq_organizations_slug"
    )
    op.execute(
        "ALTER TABLE user_organizations RENAME CONSTRAINT uq_user_tenant TO uq_user_organization"
    )
    op.execute(
        "ALTER TABLE organization_wazuh_configs "
        "RENAME CONSTRAINT uq_tenant_wazuh_config_tenant "
        "TO uq_organization_wazuh_config_organization"
    )

    # ── FK constraints (auto-named by Postgres) ───────────────────────────────
    # Pattern: <table>_<column>_fkey. After the table+column renames above the
    # legacy names (e.g. user_tenants_tenant_id_fkey) still exist; rename them
    # for naming consistency with the new schema.
    op.execute(
        "ALTER TABLE user_organizations "
        "RENAME CONSTRAINT user_tenants_user_id_fkey "
        "TO user_organizations_user_id_fkey"
    )
    op.execute(
        "ALTER TABLE user_organizations "
        "RENAME CONSTRAINT user_tenants_tenant_id_fkey "
        "TO user_organizations_organization_id_fkey"
    )
    op.execute(
        "ALTER TABLE organization_wazuh_configs "
        "RENAME CONSTRAINT tenant_wazuh_configs_tenant_id_fkey "
        "TO organization_wazuh_configs_organization_id_fkey"
    )

    # ── indexes ───────────────────────────────────────────────────────────────
    # ALTER INDEX ... RENAME TO ... preserves the underlying B-tree / HNSW /
    # GIN structure — no rebuild.
    op.execute("ALTER INDEX ix_tenants_slug RENAME TO ix_organizations_slug")
    op.execute("ALTER INDEX ix_user_tenants_user_id RENAME TO ix_user_organizations_user_id")
    op.execute(
        "ALTER INDEX ix_user_tenants_tenant_id RENAME TO ix_user_organizations_organization_id"
    )
    op.execute(
        "ALTER INDEX ix_audit_events_tenant_created RENAME TO ix_audit_events_organization_created"
    )
    op.execute(
        "ALTER INDEX ix_tenant_wazuh_configs_tenant_id "
        "RENAME TO ix_organization_wazuh_configs_organization_id"
    )
    op.execute(
        "ALTER INDEX ix_knowledge_chunks_tenant_source "
        "RENAME TO ix_knowledge_chunks_organization_source"
    )
    op.execute(
        "ALTER INDEX ix_knowledge_chunks_tenant_id RENAME TO ix_knowledge_chunks_organization_id"
    )


def downgrade() -> None:
    # Reverse of upgrade() — same operations in reverse order.

    # ── indexes ───────────────────────────────────────────────────────────────
    op.execute(
        "ALTER INDEX ix_knowledge_chunks_organization_id RENAME TO ix_knowledge_chunks_tenant_id"
    )
    op.execute(
        "ALTER INDEX ix_knowledge_chunks_organization_source "
        "RENAME TO ix_knowledge_chunks_tenant_source"
    )
    op.execute(
        "ALTER INDEX ix_organization_wazuh_configs_organization_id "
        "RENAME TO ix_tenant_wazuh_configs_tenant_id"
    )
    op.execute(
        "ALTER INDEX ix_audit_events_organization_created RENAME TO ix_audit_events_tenant_created"
    )
    op.execute(
        "ALTER INDEX ix_user_organizations_organization_id RENAME TO ix_user_tenants_tenant_id"
    )
    op.execute("ALTER INDEX ix_user_organizations_user_id RENAME TO ix_user_tenants_user_id")
    op.execute("ALTER INDEX ix_organizations_slug RENAME TO ix_tenants_slug")

    # ── FK constraints ────────────────────────────────────────────────────────
    op.execute(
        "ALTER TABLE organization_wazuh_configs "
        "RENAME CONSTRAINT organization_wazuh_configs_organization_id_fkey "
        "TO tenant_wazuh_configs_tenant_id_fkey"
    )
    op.execute(
        "ALTER TABLE user_organizations "
        "RENAME CONSTRAINT user_organizations_organization_id_fkey "
        "TO user_tenants_tenant_id_fkey"
    )
    op.execute(
        "ALTER TABLE user_organizations "
        "RENAME CONSTRAINT user_organizations_user_id_fkey "
        "TO user_tenants_user_id_fkey"
    )

    # ── named unique constraints ──────────────────────────────────────────────
    op.execute(
        "ALTER TABLE organization_wazuh_configs "
        "RENAME CONSTRAINT uq_organization_wazuh_config_organization "
        "TO uq_tenant_wazuh_config_tenant"
    )
    op.execute(
        "ALTER TABLE user_organizations RENAME CONSTRAINT uq_user_organization TO uq_user_tenant"
    )
    op.execute(
        "ALTER TABLE organizations RENAME CONSTRAINT uq_organizations_slug TO uq_tenants_slug"
    )

    # ── columns ───────────────────────────────────────────────────────────────
    op.alter_column("knowledge_chunks", "organization_id", new_column_name="tenant_id")
    op.alter_column(
        "organization_wazuh_configs",
        "inject_organization_filter",
        new_column_name="inject_tenant_filter",
    )
    op.alter_column(
        "organization_wazuh_configs",
        "organization_id",
        new_column_name="tenant_id",
    )
    op.alter_column("audit_events", "organization_id", new_column_name="tenant_id")
    op.alter_column("user_organizations", "organization_id", new_column_name="tenant_id")

    # ── tables ────────────────────────────────────────────────────────────────
    op.rename_table("organization_wazuh_configs", "tenant_wazuh_configs")
    op.rename_table("user_organizations", "user_tenants")
    op.rename_table("organizations", "tenants")
