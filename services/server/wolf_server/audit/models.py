"""Audit event SQLAlchemy model.

The audit table is append-only.  Nothing in the application layer may
UPDATE or DELETE from it.  New information referencing an old event must
be written as a new event with a `related_event_id` pointer.

Design constraints (from doc 11, Rule 7):
- Code in wolf-server can write audit events.
- Nothing may delete or modify an existing event.
- The write API accepts only appends.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from wolf_server.database import Base

# Use JSONB on Postgres (binary, indexable, queryable) and fall back to
# generic JSON on SQLite (used by the test suite — SQLite has no JSONB).
# The migration declared JSONB directly; this with_variant() construction
# matches that exactly while keeping the test path working.
_JSON_TYPE = JSONB().with_variant(JSON(), "sqlite")


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class AuditEvent(Base):
    """Immutable audit record.

    Every action of significance is written here.  The table is
    INSERT-only at the application level — UPDATE and DELETE are not
    called anywhere in Wolf code.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        # Fast lookup by organization + time (most common query pattern)
        Index("ix_audit_events_organization_created", "organization_id", "created_at"),
        # Fast lookup by user
        Index("ix_audit_events_user", "user_id"),
        # Fast lookup by event type (useful for security monitoring)
        Index("ix_audit_events_type", "event_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    # organization_id is nullable to allow system-level events (startup, health checks).
    # Any event touching organization data MUST have organization_id set.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    # user_id is nullable for system-initiated events.
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    # Dotted event type, e.g. "auth.login.success", "auth.login.failure", "tool.call.read"
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # Free-form JSON payload.  Keep it compact; large data should be referenced, not embedded.
    # Uses JSONB on Postgres (via migration) for indexing support; JSON type for SQLite compat.
    event_data: Mapped[dict[str, object] | None] = mapped_column(_JSON_TYPE, nullable=True)
    # Optional link to a previous event this one supersedes or annotates.
    related_event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    # Source IP of the request, for forensic purposes.
    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    # Opaque session identifier for correlating events within one session.
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Immutable timestamp — set at write time, never updated.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    def __repr__(self) -> str:
        return (
            f"<AuditEvent id={self.id} type={self.event_type!r} "
            f"organization={self.organization_id} at={self.created_at}>"
        )
