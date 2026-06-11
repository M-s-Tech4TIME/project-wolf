"""Immutable organization context — the central isolation primitive.

An OrganizationContext is created once when a request is authenticated and is
injected into every downstream operation.  It is frozen (immutable) so that
nothing downstream can change which organization's data is being accessed.

Rule from doc 05: The model never names, picks, or influences which
organization's data is touched.  This module enforces that by making the
context a frozen dataclass that is set by wolf-server from the session, never
from any model output.

Usage:
    # In a FastAPI dependency:
    ctx = await require_organization_context(request, db)
    # Pass ctx to every tool call; do NOT extract organization_id from model output.
"""

import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from wolf_server.database import get_db
from wolf_server.organization.models import Organization, User, UserOrganization

# Valid roles — extend as new roles are defined.
Role = Literal["analyst", "approver", "admin", "superuser"]

VALID_ROLES: frozenset[str] = frozenset({"analyst", "approver", "admin", "superuser"})


@dataclass(frozen=True)
class OrganizationContext:
    """Immutable context stamped onto every request.

    Frozen so that downstream code cannot change which organization's data is
    accessed.  All fields are set from the authenticated session — never from
    model output or request parameters.
    """

    organization_id: uuid.UUID
    organization_slug: str
    user_id: uuid.UUID
    user_email: str
    role: str
    session_id: str  # opaque token for correlation in audit records

    def __post_init__(self) -> None:
        if self.role not in VALID_ROLES:
            msg = f"Invalid role {self.role!r}; allowed: {sorted(VALID_ROLES)}"
            raise ValueError(msg)


# ── FastAPI dependencies ─────────────────────────────────────────────────────


async def require_organization_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> OrganizationContext:
    """FastAPI dependency: extract and validate the organization context from the session.

    Raises HTTP 401 if unauthenticated, HTTP 403 if the user is not a member
    of the requested organization.

    The organization_id is ALWAYS taken from the session — never from query
    params, request body, or any model output.
    """
    # Session payload is set by the auth middleware after JWT validation.
    session: dict[str, object] = getattr(request.state, "session", {})
    user_id_raw = session.get("user_id")
    organization_id_raw = session.get("organization_id")
    session_id = str(session.get("session_id", ""))

    if not user_id_raw or not organization_id_raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )

    try:
        user_id = uuid.UUID(str(user_id_raw))
        organization_id = uuid.UUID(str(organization_id_raw))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session"
        ) from None

    # Load the user-organization binding to get the role.
    result = await db.execute(
        select(UserOrganization)
        .where(
            UserOrganization.user_id == user_id,
            UserOrganization.organization_id == organization_id,
        )
        .options(
            selectinload(UserOrganization.user),
            selectinload(UserOrganization.organization),
        )
    )
    binding = result.scalar_one_or_none()

    if binding is None or not binding.user.is_active or not binding.organization.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this organization",
        )

    return OrganizationContext(
        organization_id=organization_id,
        organization_slug=binding.organization.slug,
        user_id=user_id,
        user_email=binding.user.email,
        role=binding.role,
        session_id=session_id,
    )


async def require_active_organization(
    organization_id: uuid.UUID,
    db: AsyncSession,
) -> Organization:
    """Load an Organization, raising 404 if absent or inactive."""
    result = await db.execute(
        select(Organization).where(
            Organization.id == organization_id, Organization.is_active.is_(True)
        )
    )
    organization = result.scalar_one_or_none()
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization {organization_id} not found",
        )
    return organization


async def require_active_user(
    user_id: uuid.UUID,
    db: AsyncSession,
) -> User:
    """Load a User, raising 404 if absent or inactive."""
    result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )
    return user
