"""SQLAlchemy models for the organization data model.

Tables:
  organizations       — one record per isolated customer (or single-org)
  users               — platform users (local accounts or OIDC-federated)
  user_organizations  — many-to-many: user ↔ organization with a role
                        (analyst / responder / engineer / admin / superuser —
                        validated app-side, see organization/rbac.py)

Design notes:
  - All PKs are UUID so records can be created without a database round-trip.
  - Timestamps are always timezone-aware (UTC).  The DTZ ruff rule enforces this.
  - organization_id appears on every data table; the audit table is no exception.
  - Connection profiles (Wazuh credentials) are NOT stored here — they live in
    the secrets backend, keyed by organization_id.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from wolf_server.database import Base


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Organization(Base):
    """One isolated customer / deployment."""

    __tablename__ = "organizations"
    # Migration 0001 created both a named UniqueConstraint and a
    # separate non-unique Index on `slug`. Declare them explicitly
    # here so `alembic check` doesn't see drift.
    __table_args__ = (
        UniqueConstraint("slug", name="uq_organizations_slug"),
        Index("ix_organizations_slug", "slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    user_organizations: Mapped[list["UserOrganization"]] = relationship(
        "UserOrganization", back_populates="organization", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Organization id={self.id} slug={self.slug!r}>"


class User(Base):
    """A platform user — local account or OIDC-federated."""

    __tablename__ = "users"
    # Migration 0001 created both a named UniqueConstraint on `email`
    # and a separate non-unique Index. Declare them explicitly here
    # so `alembic check` doesn't see drift. oidc_sub gets a named
    # UniqueConstraint too (no separate index — the constraint is
    # sufficient since OIDC lookups don't need a non-unique fallback).
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_email", "email"),
        UniqueConstraint("oidc_sub", name="uq_users_oidc_sub"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Null when the user authenticates exclusively via OIDC.
    hashed_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    # OIDC subject claim — null for local-only accounts.
    oidc_sub: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Superusers can administer all organizations; use sparingly.
    is_superuser: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    user_organizations: Mapped[list["UserOrganization"]] = relationship(
        "UserOrganization", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"


class UserOrganization(Base):
    """Binding of a user to an organization with a specific role."""

    __tablename__ = "user_organizations"
    __table_args__ = (UniqueConstraint("user_id", "organization_id", name="uq_user_organization"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Role within this organization.  Validated at the application layer against
    # the set of allowed roles.  Stored as a plain string so new roles can be
    # added without schema migrations.
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    user: Mapped["User"] = relationship("User", back_populates="user_organizations")
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="user_organizations"
    )

    def __repr__(self) -> str:
        return (
            f"<UserOrganization user={self.user_id} organization={self.organization_id} "
            f"role={self.role!r}>"
        )
