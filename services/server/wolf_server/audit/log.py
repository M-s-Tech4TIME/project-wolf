"""Audit log writer — append-only, organization-scoped.

All audit writes go through `write_event`.  It is an async function that
inserts a row and never updates or deletes.

Call `write_event` from route handlers, middleware, and the agent loop.
When in doubt, write the event — storage is cheap; forensic gaps are not.
"""

import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from wolf_server.audit.models import AuditEvent
from wolf_server.organization.context import OrganizationContext

logger = structlog.get_logger(__name__)


async def write_event(
    db: AsyncSession,
    *,
    event_type: str,
    event_data: dict[str, Any] | None = None,
    organization_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    session_id: str | None = None,
    source_ip: str | None = None,
    related_event_id: uuid.UUID | None = None,
) -> AuditEvent:
    """Insert an audit event and flush (but do not commit — the caller's
    transaction commits the event together with any associated state change).

    For standalone audit writes (no outer transaction), callers must call
    `await db.commit()` after this function returns.
    """
    event = AuditEvent(
        event_type=event_type,
        event_data=event_data,
        organization_id=organization_id,
        user_id=user_id,
        session_id=session_id,
        source_ip=source_ip,
        related_event_id=related_event_id,
    )
    db.add(event)
    await db.flush()

    logger.info(
        "audit_event",
        event_id=str(event.id),
        event_type=event_type,
        organization_id=str(organization_id) if organization_id else None,
        user_id=str(user_id) if user_id else None,
    )
    return event


async def write_event_from_context(
    db: AsyncSession,
    ctx: OrganizationContext,
    *,
    event_type: str,
    event_data: dict[str, Any] | None = None,
    source_ip: str | None = None,
    related_event_id: uuid.UUID | None = None,
) -> AuditEvent:
    """Convenience wrapper: populate organization/user/session from a OrganizationContext."""
    return await write_event(
        db,
        event_type=event_type,
        event_data=event_data,
        organization_id=ctx.organization_id,
        user_id=ctx.user_id,
        session_id=ctx.session_id,
        source_ip=source_ip,
        related_event_id=related_event_id,
    )


async def write_dual_event(
    db: AsyncSession,
    *,
    event_type: str,
    event_data: dict[str, Any],
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None,
    session_id: str | None = None,
    source_ip: str | None = None,
) -> AuditEvent:
    """Write a governance event into BOTH the org's audit view and the
    install-level audit (ADR 0018 role-change discipline).

    The org-level row carries ``organization_id``; the install-level row
    carries ``organization_id=None`` (so it surfaces in the Superuser's
    install-wide view), embeds the org id in its ``event_data``, and links
    back to the org row via ``related_event_id``.  Returns the org-level
    event.  The caller owns the surrounding transaction (commit).
    """
    org_event = await write_event(
        db,
        event_type=event_type,
        event_data=event_data,
        organization_id=organization_id,
        user_id=user_id,
        session_id=session_id,
        source_ip=source_ip,
    )
    await write_event(
        db,
        event_type=event_type,
        event_data={**event_data, "organization_id": str(organization_id)},
        organization_id=None,
        user_id=user_id,
        session_id=session_id,
        source_ip=source_ip,
        related_event_id=org_event.id,
    )
    return org_event
