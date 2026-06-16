"""Immutable organization context — the central isolation primitive.

An OrganizationContext is created once when a request is authenticated and is
injected into every downstream operation.  It is frozen (immutable) so that
nothing downstream can change which organization's data is being accessed.

Rule from doc 05: The model never names, picks, or influences which
organization's data is touched.  This module enforces that by making the
context a frozen dataclass that is set by wolf-server from the request's
authenticated identity, never from any model output.

Phase 6.5-c (ADR 0018 Round 3): the session cookie carries
AUTHENTICATION only; the active organization arrives per request in the
`X-Organization-Id` header, set by the dashboard from per-tab state — so
two tabs can work in two different organizations concurrently.  The
header names the org; it never grants access: the membership binding is
validated on every request exactly as before.  The transitional JWT
org-claim fallback shipped with 6.5-c-i was removed when 6.5-c-ii
signed off — the header is now the ONLY way to name an organization.

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
from wolf_server.organization.superuser_access import expire_if_past

# Valid roles per ADR 0018 (Phase 6.5-b): "approver" was renamed to
# "responder" and "engineer" was added (data migration 0008 rewrites
# existing rows).  "superuser" marks the Superuser's own consented
# membership row — see organization/rbac.py for what each role can do.
Role = Literal["analyst", "responder", "engineer", "admin", "superuser"]

VALID_ROLES: frozenset[str] = frozenset({"analyst", "responder", "engineer", "admin", "superuser"})


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

ORG_HEADER = "X-Organization-Id"


async def require_organization_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> OrganizationContext:
    """FastAPI dependency: extract and validate the organization context.

    The cookie (session) identifies the USER; the `X-Organization-Id`
    header names the ORGANIZATION for this request (per-tab context,
    ADR 0018).  The membership binding is validated on every request —
    the header selects among the user's memberships, it can never reach
    beyond them.

    Raises 401 if unauthenticated or the header is absent, 400 if the
    header is malformed, 403 if the user is not an active member of the
    named organization.

    The organization_id comes ONLY from the header — never from the
    session, query params, request body, or any model output.
    """
    # Session payload is set by the auth middleware after JWT validation.
    session: dict[str, object] = getattr(request.state, "session", {})
    user_id_raw = session.get("user_id")
    session_id = str(session.get("session_id", ""))

    if not user_id_raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )

    # Per-tab org context: the header is the ONLY source of the
    # organization id (the session is authentication only).
    header_raw = request.headers.get(ORG_HEADER)
    if header_raw is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "No organization context — send the "
                f"{ORG_HEADER} header with an organization you belong to"
            ),
        )
    try:
        organization_id = uuid.UUID(header_raw.strip())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {ORG_HEADER} header: not a UUID",
        ) from None

    try:
        user_id = uuid.UUID(str(user_id_raw))
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

    # Phase 6.5-f: a time-limited Superuser grant past its deadline is
    # pruned lazily here (no background scheduler), so the Superuser is
    # locked out on their very next request.  No-op for every other
    # binding — see organization/superuser_access.py.
    if await expire_if_past(db, binding):
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this organization",
        )

    # Phase 6.5-h (ADR 0018 item 9): the invite-verification gate.  An
    # Admin-created account is "unverified" until the user pastes their
    # invite link (POST /api/v1/auth/verify-invite).  This dependency is
    # the chokepoint for ALL org data (chat, member management, audit, …),
    # so enforcing here covers every org-scoped endpoint in one place —
    # `binding.user` is already eager-loaded above, so this costs no extra
    # query.  ADR 0018 framed this as middleware; the per-request authz
    # chokepoint is the cleaner fit for this codebase (it mirrors the
    # lazy-expiry hook just above) and naturally exempts exactly the
    # self-service endpoints an unverified user still needs — /me,
    # /me/organizations, verify-invite, logout (they read the raw session,
    # not this dependency).  Superuser-only endpoints (organizations.py,
    # superuser.py) gate on require_superuser and the Superuser is always
    # "verified", so they are exempt by construction.
    if binding.user.verification_status != "verified":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Verify your account with the invitation link your "
                "administrator sent you before accessing this organization."
            ),
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
