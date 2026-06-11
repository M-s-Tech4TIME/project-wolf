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

  FK constraints (renamed by DYNAMIC lookup, not hardcoded name):
    The current FK name depends on when the database was created. Databases
    initialised before the Base naming_convention landed (2026-06-05) carry
    Postgres auto-names (user_tenants_user_id_fkey); fresh databases get
    convention names from wolf_server.database.NAMING_CONVENTION
    (fk_user_tenants_user_id_users). This migration looks up whichever FK
    actually exists on the (table, columns) pair via pg_constraint and renames
    it to the convention name for the NEW table:
      user_organizations(user_id)
          → fk_user_organizations_user_id_users
      user_organizations(organization_id)
          → fk_user_organizations_organization_id_organizations
      organization_wazuh_configs(organization_id)
          → fk_organization_wazuh_configs_organization_id_organizations

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


def _rename_fk(table: str, columns: str, new_name: str) -> None:
    """Rename the FK constraint on (table, columns) to new_name, whatever it
    is currently called.

    `columns` is the comma-separated attnum-ordered column list, e.g.
    "user_id". The DO block resolves the constraint's current name from
    pg_constraint — necessary because old databases carry Postgres
    auto-names (<table>_<col>_fkey) while fresh ones carry
    NAMING_CONVENTION names (fk_<table>_<col>_<reftable>).
    """
    # S608: the interpolated values are compile-time string literals defined
    # in this migration file (table/column/constraint names), never external
    # or user-controlled input — the injection vector ruff flags can't occur.
    op.execute(
        f"""
        DO $$
        DECLARE
            fk_name text;
        BEGIN
            SELECT con.conname INTO fk_name
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            WHERE rel.relname = '{table}'
              AND con.contype = 'f'
              AND (
                  SELECT string_agg(att.attname, ',' ORDER BY u.ord)
                  FROM unnest(con.conkey) WITH ORDINALITY AS u(attnum, ord)
                  JOIN pg_attribute att
                    ON att.attrelid = con.conrelid AND att.attnum = u.attnum
              ) = '{columns}';
            IF fk_name IS NULL THEN
                RAISE EXCEPTION
                    'No FK constraint found on {table}({columns})';
            END IF;
            IF fk_name <> '{new_name}' THEN
                EXECUTE format(
                    'ALTER TABLE {table} RENAME CONSTRAINT %I TO %I',
                    fk_name, '{new_name}'
                );
            END IF;
        END $$;
        """  # noqa: S608
    )


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

    # ── FK constraints (dynamic rename) ───────────────────────────────────────
    # The legacy FK name varies by database age (Postgres auto-name vs
    # NAMING_CONVENTION name — see the module docstring). Look up whatever
    # exists on the column pair and rename it to the convention name for the
    # NEW table, so every post-0007 database converges on identical names.
    _rename_fk("user_organizations", "user_id", "fk_user_organizations_user_id_users")
    _rename_fk(
        "user_organizations",
        "organization_id",
        "fk_user_organizations_organization_id_organizations",
    )
    _rename_fk(
        "organization_wazuh_configs",
        "organization_id",
        "fk_organization_wazuh_configs_organization_id_organizations",
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

    # ── FK constraints (dynamic rename) ───────────────────────────────────────
    # Downgrade restores the NAMING_CONVENTION names a fresh pre-0007
    # database would have (fk_<old_table>_<col>_<reftable>). Databases that
    # originally carried Postgres auto-names don't get those back — the
    # convention name is the canonical pre-0007 shape going forward.
    _rename_fk(
        "organization_wazuh_configs",
        "organization_id",
        "fk_tenant_wazuh_configs_tenant_id_tenants",
    )
    _rename_fk(
        "user_organizations",
        "organization_id",
        "fk_user_tenants_tenant_id_tenants",
    )
    _rename_fk("user_organizations", "user_id", "fk_user_tenants_user_id_users")

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
