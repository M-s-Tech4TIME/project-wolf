"""Org-scoped management routes — Phase 6.5-b, ADR 0018.

Everything here is scoped to the caller's authenticated organization
context and gated by the capability matrix (organization/rbac.py):

  USERS_MANAGE (Admin):
    GET    /api/v1/organization/users               — list members + roles
    POST   /api/v1/organization/users               — create user / add member
    PATCH  /api/v1/organization/users/{id}/role     — change a member's role
    DELETE /api/v1/organization/users/{id}          — remove a membership

  SUPERUSER_MEMBERSHIP_GRANT (Admin) — the org-consent gate:
    POST   /api/v1/organization/memberships/superuser   — grant
    DELETE /api/v1/organization/memberships/superuser   — revoke

  AUDIT_LOG_VIEW (Admin, Responder):
    GET    /api/v1/organization/audit               — the org's audit trail

Role-change discipline (ADR 0018): the "Last Admin" invariant guard
refuses any demotion/removal that would leave the org with zero active
Admins; role changes and Superuser-membership changes are audit-logged
in BOTH the install audit (organization_id=None) and the org's audit,
linked via related_event_id.  Time-limited Superuser grants (24h expiry)
and member notifications arrive with 6.5-f.
"""

import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from wolf_server.audit.log import write_event
from wolf_server.audit.models import AuditEvent
from wolf_server.auth.blacklist import get_session_blacklist
from wolf_server.auth.local import hash_password
from wolf_server.config import get_settings
from wolf_server.database import get_db
from wolf_server.organization.context import OrganizationContext
from wolf_server.organization.models import User, UserOrganization
from wolf_server.organization.rbac import (
    Capability,
    ensure_not_last_admin,
    require_capability,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/organization", tags=["org-management"])

# Matches the bootstrap CLI's entropy (24 bytes -> 32 url-safe chars).
_PASSWORD_BYTES = 24

# Roles an org Admin may hand out.  "superuser" is deliberately absent:
# the Superuser's membership goes through the dedicated consent-gate
# endpoints below, and no ordinary user may ever carry that role value.
ASSIGNABLE_ROLES: frozenset[str] = frozenset({"admin", "engineer", "responder", "analyst"})


# ── Schemas ──────────────────────────────────────────────────────────────────


class MemberResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str
    is_active: bool
    member_since: datetime


class MemberCreateRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=255)
    role: str


class MemberCreateResponse(MemberResponse):
    # Set only when a brand-new user account was created; returned once
    # for out-of-band delivery, never logged or persisted in plaintext.
    new_password: str | None


class PasswordResetResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    # The freshly generated password — returned ONCE for out-of-band
    # delivery to the member; never logged or persisted in plaintext.
    new_password: str


class RoleChangeRequest(BaseModel):
    role: str


class SuperuserMembershipResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    role: str


class AuditEventResponse(BaseModel):
    id: uuid.UUID
    event_type: str
    event_data: dict[str, Any] | None
    user_id: uuid.UUID | None
    source_ip: str | None
    related_event_id: uuid.UUID | None
    created_at: datetime


class AuditPageResponse(BaseModel):
    events: list[AuditEventResponse]
    limit: int
    offset: int


# ── Helpers ──────────────────────────────────────────────────────────────────


def _source_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _revoke_all_sessions(user_id: uuid.UUID) -> None:
    """Watermark-revoke every outstanding session for a user (6.5-g).

    Mirrors api/superuser._revoke_all_sessions — a password reset must
    invalidate live sessions (the TTL covers the access-token lifetime).
    """
    ttl = get_settings().access_token_expire_minutes * 60
    await get_session_blacklist().revoke_user(str(user_id), ttl_seconds=ttl)


def _validate_assignable_role(role: str) -> None:
    if role not in ASSIGNABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Role must be one of {sorted(ASSIGNABLE_ROLES)}; got {role!r}",
        )


async def _get_binding(
    db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID
) -> UserOrganization | None:
    binding: UserOrganization | None = await db.scalar(
        select(UserOrganization)
        .where(
            UserOrganization.organization_id == organization_id,
            UserOrganization.user_id == user_id,
        )
        .options(selectinload(UserOrganization.user))
    )
    return binding


async def _write_dual_audit(
    db: AsyncSession,
    ctx: OrganizationContext,
    *,
    event_type: str,
    event_data: dict[str, Any],
    source_ip: str | None,
) -> None:
    """ADR 0018 role-change discipline: governance events land in BOTH the
    org's audit view and the install-level audit (organization_id=None),
    linked via related_event_id."""
    org_event = await write_event(
        db,
        event_type=event_type,
        event_data=event_data,
        organization_id=ctx.organization_id,
        user_id=ctx.user_id,
        session_id=ctx.session_id,
        source_ip=source_ip,
    )
    await write_event(
        db,
        event_type=event_type,
        event_data={**event_data, "organization_id": str(ctx.organization_id)},
        organization_id=None,
        user_id=ctx.user_id,
        session_id=ctx.session_id,
        source_ip=source_ip,
        related_event_id=org_event.id,
    )


def _member_response(binding: UserOrganization) -> MemberResponse:
    return MemberResponse(
        user_id=binding.user_id,
        email=binding.user.email,
        display_name=binding.user.display_name,
        role=binding.role,
        is_active=binding.user.is_active,
        member_since=binding.created_at,
    )


# ── User management (Admin) ──────────────────────────────────────────────────


@router.get("/users", response_model=list[MemberResponse])
async def list_members(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[OrganizationContext, Depends(require_capability(Capability.USERS_MANAGE))],
) -> list[MemberResponse]:
    """List every member of the caller's organization with their role."""
    result = await db.execute(
        select(UserOrganization)
        .where(UserOrganization.organization_id == ctx.organization_id)
        .options(selectinload(UserOrganization.user))
        .order_by(UserOrganization.created_at)
    )
    return [_member_response(b) for b in result.scalars()]


@router.post("/users", response_model=MemberCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_member(
    body: MemberCreateRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[OrganizationContext, Depends(require_capability(Capability.USERS_MANAGE))],
) -> MemberCreateResponse:
    """Create a user (or add an existing one) as a member of this org.

    A brand-new account gets a generated password returned ONCE in the
    response.  Adding the install Superuser this way is refused — that is
    what the consent-gate endpoints are for.
    """
    _validate_assignable_role(body.role)

    now = datetime.now(UTC)
    new_password: str | None = None
    user = await db.scalar(select(User).where(User.email == body.email))
    if user is None:
        new_password = secrets.token_urlsafe(_PASSWORD_BYTES)
        user = User(
            id=uuid.uuid4(),
            email=body.email,
            display_name=body.display_name,
            hashed_password=hash_password(new_password),
            is_active=True,
            is_superuser=False,
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        await db.flush()
    elif user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The Superuser's membership goes through the consent-gate "
                "endpoint POST /api/v1/organization/memberships/superuser"
            ),
        )

    existing = await _get_binding(db, ctx.organization_id, user.id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{body.email} is already a member of this organization",
        )

    binding = UserOrganization(
        id=uuid.uuid4(),
        user_id=user.id,
        organization_id=ctx.organization_id,
        role=body.role,
        created_at=now,
    )
    db.add(binding)
    await db.flush()

    await _write_dual_audit(
        db,
        ctx,
        event_type="organization.member.added",
        event_data={
            "member_user_id": str(user.id),
            "member_email": user.email,
            "role": body.role,
            "created_new_user": new_password is not None,
        },
        source_ip=_source_ip(request),
    )
    await db.commit()

    logger.info(
        "organization_member_added",
        organization_id=str(ctx.organization_id),
        member_user_id=str(user.id),
        role=body.role,
    )
    return MemberCreateResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=body.role,
        is_active=user.is_active,
        member_since=now,
        new_password=new_password,
    )


@router.patch("/users/{user_id}/role", response_model=MemberResponse)
async def change_member_role(
    user_id: uuid.UUID,
    body: RoleChangeRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[OrganizationContext, Depends(require_capability(Capability.USERS_MANAGE))],
) -> MemberResponse:
    """Change a member's role (Last-Admin guard enforced)."""
    _validate_assignable_role(body.role)

    binding = await _get_binding(db, ctx.organization_id, user_id)
    if binding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} is not a member of this organization",
        )
    if binding.role == "superuser":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The Superuser's membership role is fixed; revoke it via "
                "DELETE /api/v1/organization/memberships/superuser instead"
            ),
        )
    if binding.role == body.role:
        return _member_response(binding)

    # Demoting an Admin: the org must keep at least one active Admin.
    if binding.role == "admin":
        await ensure_not_last_admin(db, ctx.organization_id, user_id)

    old_role = binding.role
    binding.role = body.role

    await _write_dual_audit(
        db,
        ctx,
        event_type="organization.member.role_changed",
        event_data={
            "member_user_id": str(user_id),
            "member_email": binding.user.email,
            "old_role": old_role,
            "new_role": body.role,
        },
        source_ip=_source_ip(request),
    )
    await db.commit()

    logger.info(
        "organization_member_role_changed",
        organization_id=str(ctx.organization_id),
        member_user_id=str(user_id),
        old_role=old_role,
        new_role=body.role,
    )
    return _member_response(binding)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    user_id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[OrganizationContext, Depends(require_capability(Capability.USERS_MANAGE))],
) -> None:
    """Remove a member from this organization (the account itself survives)."""
    binding = await _get_binding(db, ctx.organization_id, user_id)
    if binding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} is not a member of this organization",
        )
    if binding.role == "superuser":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Revoke the Superuser's membership via "
                "DELETE /api/v1/organization/memberships/superuser instead"
            ),
        )
    if binding.role == "admin":
        await ensure_not_last_admin(db, ctx.organization_id, user_id)

    member_email = binding.user.email
    removed_role = binding.role
    await db.delete(binding)

    await _write_dual_audit(
        db,
        ctx,
        event_type="organization.member.removed",
        event_data={
            "member_user_id": str(user_id),
            "member_email": member_email,
            "removed_role": removed_role,
        },
        source_ip=_source_ip(request),
    )
    await db.commit()

    logger.info(
        "organization_member_removed",
        organization_id=str(ctx.organization_id),
        member_user_id=str(user_id),
    )


@router.post(
    "/users/{user_id}/password-reset",
    response_model=PasswordResetResponse,
)
async def reset_member_password(
    user_id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[OrganizationContext, Depends(require_capability(Capability.USERS_MANAGE))],
) -> PasswordResetResponse:
    """Admin resets a member's password (Phase 6.5-e.1).

    Recovery path for a member who forgot their password — Wolf has no
    SMTP / self-service reset, so an Admin rotates it and delivers the
    one-time password out of band. Scoped to THIS org via the membership
    binding (an Admin can only reset members of their own org); the
    Superuser's consent-granted membership is off-limits here (rotate the
    Superuser credential via the bootstrap CLI on the host). The reset
    blacklists the member's live sessions and is audited in both the org
    and install logs.
    """
    binding = await _get_binding(db, ctx.organization_id, user_id)
    if binding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} is not a member of this organization",
        )
    if binding.role == "superuser" or binding.user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The Superuser password is rotated via the bootstrap_superuser "
                "CLI on the host, not via the API"
            ),
        )

    new_password = secrets.token_urlsafe(_PASSWORD_BYTES)
    binding.user.hashed_password = hash_password(new_password)
    binding.user.updated_at = datetime.now(UTC)

    # A reset invalidates the member's live sessions (ADR 0018 Round 1).
    await _revoke_all_sessions(binding.user_id)

    await _write_dual_audit(
        db,
        ctx,
        event_type="organization.member.password_reset",
        event_data={
            "member_user_id": str(user_id),
            "member_email": binding.user.email,
            "sessions_revoked": True,
        },
        source_ip=_source_ip(request),
    )
    await db.commit()

    logger.info(
        "organization_member_password_reset",
        organization_id=str(ctx.organization_id),
        member_user_id=str(user_id),
    )
    return PasswordResetResponse(
        user_id=binding.user_id,
        email=binding.user.email,
        new_password=new_password,
    )


# ── Superuser membership: the org-consent gate (Admin) ───────────────────────


async def _get_install_superuser(db: AsyncSession) -> User:
    result = await db.execute(
        select(User).where(User.is_superuser.is_(True), User.is_active.is_(True))
    )
    superusers = list(result.scalars())
    if len(superusers) != 1:
        # ADR 0018: exactly one install Superuser ("Wolf") exists.  Zero
        # means bootstrap never ran; more than one means manual DB edits.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Expected exactly one active Superuser account, found "
                f"{len(superusers)} — run the bootstrap_superuser CLI on the host"
            ),
        )
    return superusers[0]


@router.post(
    "/memberships/superuser",
    response_model=SuperuserMembershipResponse,
    status_code=status.HTTP_201_CREATED,
)
async def grant_superuser_membership(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[
        OrganizationContext, Depends(require_capability(Capability.SUPERUSER_MEMBERSHIP_GRANT))
    ],
) -> SuperuserMembershipResponse:
    """Grant the install Superuser read/chat membership in this org.

    This is the consent gate from ADR 0018: only an org Admin can extend
    the Superuser's reach into the org's data, and the audit trail (both
    org and install level) records exactly who consented and when.
    """
    superuser = await _get_install_superuser(db)
    existing = await _get_binding(db, ctx.organization_id, superuser.id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The Superuser already has membership in this organization",
        )

    db.add(
        UserOrganization(
            id=uuid.uuid4(),
            user_id=superuser.id,
            organization_id=ctx.organization_id,
            role="superuser",
            created_at=datetime.now(UTC),
        )
    )
    await db.flush()

    await _write_dual_audit(
        db,
        ctx,
        event_type="organization.superuser_membership.granted",
        event_data={"superuser_user_id": str(superuser.id), "granted_by": str(ctx.user_id)},
        source_ip=_source_ip(request),
    )
    await db.commit()

    logger.info(
        "superuser_membership_granted",
        organization_id=str(ctx.organization_id),
        granted_by=str(ctx.user_id),
    )
    return SuperuserMembershipResponse(
        user_id=superuser.id, email=superuser.email, role="superuser"
    )


@router.delete("/memberships/superuser", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_superuser_membership(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[
        OrganizationContext, Depends(require_capability(Capability.SUPERUSER_MEMBERSHIP_GRANT))
    ],
) -> None:
    """Revoke the install Superuser's membership in this org."""
    superuser = await _get_install_superuser(db)
    binding = await _get_binding(db, ctx.organization_id, superuser.id)
    if binding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The Superuser has no membership in this organization",
        )

    await db.delete(binding)

    await _write_dual_audit(
        db,
        ctx,
        event_type="organization.superuser_membership.revoked",
        event_data={"superuser_user_id": str(superuser.id), "revoked_by": str(ctx.user_id)},
        source_ip=_source_ip(request),
    )
    await db.commit()

    logger.info(
        "superuser_membership_revoked",
        organization_id=str(ctx.organization_id),
        revoked_by=str(ctx.user_id),
    )


# ── Audit-log view (Admin, Responder) ────────────────────────────────────────


@router.get("/audit", response_model=AuditPageResponse)
async def view_audit_log(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[OrganizationContext, Depends(require_capability(Capability.AUDIT_LOG_VIEW))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPageResponse:
    """The org's own audit trail, newest first (install-level events excluded)."""
    result = await db.execute(
        select(AuditEvent)
        .where(AuditEvent.organization_id == ctx.organization_id)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id)
        .limit(limit)
        .offset(offset)
    )
    events = [
        AuditEventResponse(
            id=e.id,
            event_type=e.event_type,
            event_data=dict(e.event_data) if e.event_data is not None else None,
            user_id=e.user_id,
            source_ip=e.source_ip,
            related_event_id=e.related_event_id,
            created_at=e.created_at,
        )
        for e in result.scalars()
    ]
    return AuditPageResponse(events=events, limit=limit, offset=offset)
