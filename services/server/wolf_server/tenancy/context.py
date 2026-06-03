"""Immutable tenant context — the central isolation primitive.

A TenantContext is created once when a request is authenticated and is
injected into every downstream operation.  It is frozen (immutable) so that
nothing downstream can change which tenant's data is being accessed.

Rule from doc 05: The model never names, picks, or influences which tenant's
data is touched.  This module enforces that by making the context a frozen
dataclass that is set by wolf-server from the session, never from any
model output.

Usage:
    # In a FastAPI dependency:
    ctx = await require_tenant_context(request, db)
    # Pass ctx to every tool call; do NOT extract tenant_id from model output.
"""

import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from wolf_server.database import get_db
from wolf_server.tenancy.models import Tenant, User, UserTenant

# Valid roles — extend as new roles are defined.
Role = Literal["analyst", "approver", "admin", "superuser"]

VALID_ROLES: frozenset[str] = frozenset({"analyst", "approver", "admin", "superuser"})


@dataclass(frozen=True)
class TenantContext:
    """Immutable context stamped onto every request.

    Frozen so that downstream code cannot change which tenant's data is
    accessed.  All fields are set from the authenticated session — never from
    model output or request parameters.
    """

    tenant_id: uuid.UUID
    tenant_slug: str
    user_id: uuid.UUID
    user_email: str
    role: str
    session_id: str  # opaque token for correlation in audit records

    def __post_init__(self) -> None:
        if self.role not in VALID_ROLES:
            msg = f"Invalid role {self.role!r}; allowed: {sorted(VALID_ROLES)}"
            raise ValueError(msg)


# ── FastAPI dependencies ─────────────────────────────────────────────────────


async def require_tenant_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TenantContext:
    """FastAPI dependency: extract and validate the tenant context from the session.

    Raises HTTP 401 if unauthenticated, HTTP 403 if the user is not a member
    of the requested tenant.

    The tenant_id is ALWAYS taken from the session — never from query params,
    request body, or any model output.
    """
    # Session payload is set by the auth middleware after JWT validation.
    session: dict[str, object] = getattr(request.state, "session", {})
    user_id_raw = session.get("user_id")
    tenant_id_raw = session.get("tenant_id")
    session_id = str(session.get("session_id", ""))

    if not user_id_raw or not tenant_id_raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )

    try:
        user_id = uuid.UUID(str(user_id_raw))
        tenant_id = uuid.UUID(str(tenant_id_raw))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session"
        ) from None

    # Load the user-tenant binding to get the role.
    result = await db.execute(
        select(UserTenant)
        .where(UserTenant.user_id == user_id, UserTenant.tenant_id == tenant_id)
        .options(selectinload(UserTenant.user), selectinload(UserTenant.tenant))
    )
    binding = result.scalar_one_or_none()

    if binding is None or not binding.user.is_active or not binding.tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this tenant",
        )

    return TenantContext(
        tenant_id=tenant_id,
        tenant_slug=binding.tenant.slug,
        user_id=user_id,
        user_email=binding.user.email,
        role=binding.role,
        session_id=session_id,
    )


async def require_active_tenant(
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> Tenant:
    """Load a Tenant, raising 404 if absent or inactive."""
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
    )
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tenant {tenant_id} not found",
        )
    return tenant


async def require_active_user(
    user_id: uuid.UUID,
    db: AsyncSession,
) -> User:
    """Load a User, raising 404 if absent or inactive."""
    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )
    return user
