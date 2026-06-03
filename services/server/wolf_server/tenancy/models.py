"""SQLAlchemy models for the tenant data model.

Tables:
  tenants      — one record per isolated customer (or single-org)
  users        — platform users (local accounts or OIDC-federated)
  user_tenants — many-to-many: user ↔ tenant with a role
  roles        — role definitions (analyst, approver, admin, superuser)

Design notes:
  - All PKs are UUID so records can be created without a database round-trip.
  - Timestamps are always timezone-aware (UTC).  The DTZ ruff rule enforces this.
  - tenant_id appears on every data table; the audit table is no exception.
  - Connection profiles (Wazuh credentials) are NOT stored here — they live in
    the secrets backend, keyed by tenant_id.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
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


class Tenant(Base):
    """One isolated customer / deployment."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    user_tenants: Mapped[list["UserTenant"]] = relationship(
        "UserTenant", back_populates="tenant", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Tenant id={self.id} slug={self.slug!r}>"


class User(Base):
    """A platform user — local account or OIDC-federated."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Null when the user authenticates exclusively via OIDC.
    hashed_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    # OIDC subject claim — null for local-only accounts.
    oidc_sub: Mapped[str | None] = mapped_column(String(512), nullable=True, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Superusers can administer all tenants; use sparingly.
    is_superuser: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    user_tenants: Mapped[list["UserTenant"]] = relationship(
        "UserTenant", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"


class UserTenant(Base):
    """Binding of a user to a tenant with a specific role."""

    __tablename__ = "user_tenants"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Role within this tenant.  Validated at the application layer against the
    # set of allowed roles.  Stored as a plain string so new roles can be added
    # without schema migrations.
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    user: Mapped["User"] = relationship("User", back_populates="user_tenants")
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="user_tenants")

    def __repr__(self) -> str:
        return f"<UserTenant user={self.user_id} tenant={self.tenant_id} role={self.role!r}>"
