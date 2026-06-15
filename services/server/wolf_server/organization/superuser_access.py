"""Lazy expiry for time-limited Superuser memberships (Phase 6.5-f).

ADR 0018 lets an org Admin grant the install Superuser *time-limited*
read/chat access.  Wolf has no background scheduler, so expiry is
enforced lazily at the two places the grant is observed:

  - ``require_organization_context`` (organization/context.py) — so an
    expired Superuser is locked out on their very next request; and
  - the member-facing ``GET /api/v1/organization/superuser-access``
    endpoint that feeds the transparency banner — so the banner
    self-clears for everyone in the org.

Both call :func:`expire_if_past`.  Whoever observes the grant first past
its deadline deletes the ``UserOrganization`` row and writes the
``…expired`` audit event (org + install level), so the outcome is the
same regardless of who triggers it.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from wolf_server.organization.models import SuperuserAccessRequest, UserOrganization

EXPIRED_EVENT = "organization.superuser_membership.expired"


async def mark_request_ended(
    db: AsyncSession,
    organization_id: uuid.UUID,
    superuser_user_id: uuid.UUID,
    status: str,
    ended_at: datetime,
) -> None:
    """Stamp the terminal state on the access-request behind an ended grant.

    A ``UserOrganization`` grant doesn't back-reference the request that
    produced it, so we find the (single) still-``approved`` request for this
    (org, superuser) — that's the one tied to the grant just ended — and
    flip it to ``status`` (``revoked``/``expired``) with ``ended_at``.  This
    keeps the request row the full lifecycle record the Admin/Superuser UI
    renders.  No-op if no approved request is found (e.g. a legacy grant)."""
    req = await db.scalar(
        select(SuperuserAccessRequest)
        .where(
            SuperuserAccessRequest.organization_id == organization_id,
            SuperuserAccessRequest.superuser_user_id == superuser_user_id,
            SuperuserAccessRequest.status == "approved",
        )
        .order_by(SuperuserAccessRequest.decided_at.desc())
        .limit(1)
    )
    if req is not None:
        req.status = status
        req.ended_at = ended_at


async def expire_if_past(db: AsyncSession, binding: UserOrganization) -> bool:
    """Prune ``binding`` if it is a Superuser grant past its deadline.

    Returns ``True`` (and deletes the row + writes the dual ``…expired``
    audit) when ``binding`` is a ``superuser``-role membership whose
    ``expires_at`` is non-null and in the past.  Returns ``False``
    (no-op) for every other binding: non-superuser roles, "until-revoked"
    grants (``expires_at`` is null), or grants still inside their window.

    The caller owns the surrounding transaction — this does the delete +
    audit writes but leaves the ``commit`` to the caller.
    """
    # Imported lazily: audit.log imports organization.context, which
    # imports this module — a top-level import here would close that
    # cycle.  This path only runs when a grant actually expires, so the
    # deferred import cost is irrelevant.
    from wolf_server.audit.log import write_event

    if binding.role != "superuser" or binding.expires_at is None:
        return False

    # SQLite (test DB) can hand back a naive datetime even though the
    # column is DateTime(timezone=True); treat a naive value as UTC so the
    # comparison never raises and behaves identically to Postgres.
    deadline = binding.expires_at
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    if deadline > datetime.now(UTC):
        return False

    organization_id: uuid.UUID = binding.organization_id
    superuser_user_id: uuid.UUID = binding.user_id

    await db.delete(binding)
    await mark_request_ended(db, organization_id, superuser_user_id, "expired", deadline)

    event_data = {
        "superuser_user_id": str(superuser_user_id),
        "expired_at": deadline.isoformat(),
    }
    org_event = await write_event(
        db,
        event_type=EXPIRED_EVENT,
        event_data=event_data,
        organization_id=organization_id,
        user_id=superuser_user_id,
    )
    await write_event(
        db,
        event_type=EXPIRED_EVENT,
        event_data={**event_data, "organization_id": str(organization_id)},
        organization_id=None,
        user_id=superuser_user_id,
        related_event_id=org_event.id,
    )
    return True


async def active_superuser_binding(
    db: AsyncSession, organization_id: uuid.UUID
) -> UserOrganization | None:
    """Return the org's *active* (non-expired) Superuser membership, or None.

    There is at most one ``superuser``-role row per org.  If one exists but
    has lapsed, it is pruned via :func:`expire_if_past` and None is
    returned (the caller must commit to persist the prune + audit).  The
    returned binding has its ``user`` relationship eager-loaded so callers
    can read the Superuser's email / display name without another query.
    """
    binding = await db.scalar(
        select(UserOrganization)
        .where(
            UserOrganization.organization_id == organization_id,
            UserOrganization.role == "superuser",
        )
        .options(selectinload(UserOrganization.user))
    )
    if binding is None:
        return None
    if await expire_if_past(db, binding):
        return None
    return binding
