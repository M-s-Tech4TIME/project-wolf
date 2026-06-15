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
    Integer,
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
    # Phase 6.5-f: time-limited Superuser grants (ADR 0018 consent gate).
    # Null = no expiry — all normal members, plus "until-revoked"
    # Superuser grants.  Non-null = the row auto-expires; it is pruned
    # lazily at access time (no background scheduler), see
    # organization/superuser_access.py.  Only "superuser"-role rows ever
    # carry a non-null value today.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="user_organizations")
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="user_organizations"
    )

    def __repr__(self) -> str:
        return (
            f"<UserOrganization user={self.user_id} organization={self.organization_id} "
            f"role={self.role!r}>"
        )


class SuperuserAccessRequest(Base):
    """A Superuser's request for time-limited membership in an organization.

    ADR 0018 consent gate (Phase 6.5-f): the install Superuser cannot
    self-grant data access.  They file a request that the organization's
    Admin approves — creating a time-limited UserOrganization row
    (role="superuser", expires_at) — or rejects.  This row is the durable
    record of the ask + the decision; the resulting grant itself lives on
    UserOrganization.

    At most one PENDING request per (organization, superuser) is allowed —
    enforced in the API layer (409), not via a partial unique index, to
    keep the SQLite test database happy.

    Status lifecycle (the full timeline the Admin/Superuser sees):
      pending → approved → (revoked | expired)
      pending → rejected
      pending → cancelled
    ``revoked``/``expired`` are terminal states an *approved* grant lands
    in when an Admin revokes it early or it lapses; ``ended_at`` stamps the
    moment the grant ended so the lifecycle is fully queryable from this
    one row (no need to cross-reference the audit log).
    """

    __tablename__ = "superuser_access_requests"
    # Composite index serves the Admin's "pending for this org" list and,
    # by leftmost-prefix, any "all requests for this org" query.  The
    # second index serves the Superuser's "my requests" view.  Names are
    # explicit (and match the migration) so `alembic check` sees no drift.
    __table_args__ = (
        Index(
            "ix_superuser_access_requests_org_status",
            "organization_id",
            "status",
        ),
        Index(
            "ix_superuser_access_requests_superuser_user_id",
            "superuser_user_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The install Superuser who filed the request.
    superuser_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # pending → approved → (revoked | expired) | rejected | cancelled.
    # Validated app-side; see the class docstring for the full lifecycle.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # The Superuser's justification (optional, operator-facing).
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Proposed grant duration; null = "until revoked".  The approving
    # Admin may override it — granted_expires_at records what was actually
    # granted.
    requested_duration_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # What the Admin granted on approval; null = until-revoked.  Mirrors
    # the resulting UserOrganization.expires_at at decision time.
    granted_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The Admin who approved/rejected (null while pending/cancelled).
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    # When an APPROVED grant ended — set on Admin revoke or lazy expiry.
    # Null while pending/approved-and-active/rejected/cancelled.  Completes
    # the lifecycle timeline (requested → decided → ended).
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<SuperuserAccessRequest org={self.organization_id} "
            f"status={self.status!r}>"
        )
