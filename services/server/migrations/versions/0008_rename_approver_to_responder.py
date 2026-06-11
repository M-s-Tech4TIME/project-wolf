"""Rename the "approver" role value to "responder" (Phase 6.5-b).

Per ADR 0018 §"Decision: per-organization RBAC": the Approver role was
renamed to Responder on 2026-06-10 — the incident-response role that can
chat, read org data, view the audit log, and (from Phase 6) approve
propose-actions plus execute actions directly.  Roles are stored as plain
strings on user_organizations.role (no DB enum / CHECK constraint), so
this is a pure data migration: rewrite existing rows, nothing structural.

The new "engineer" role added in the same phase needs no migration — no
rows can exist with a value that was never valid before.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE user_organizations SET role = 'responder' WHERE role = 'approver'")


def downgrade() -> None:
    op.execute("UPDATE user_organizations SET role = 'approver' WHERE role = 'responder'")
