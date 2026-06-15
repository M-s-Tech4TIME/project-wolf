"""Org-scoped management routes — Phase 6.5-b, ADR 0018.

Everything here is scoped to the caller's authenticated organization
context and gated by the capability matrix (organization/rbac.py):

  USERS_MANAGE (Admin):
    GET    /api/v1/organization/users               — list members + roles
    POST   /api/v1/organization/users               — create user / add member
    PATCH  /api/v1/organization/users/{id}/role     — change a member's role
    DELETE /api/v1/organization/users/{id}          — remove a membership

  SUPERUSER_MEMBERSHIP_GRANT (Admin) — the org-consent gate (6.5-f):
    GET    /api/v1/organization/access-requests            — Superuser requests for this org
    POST   /api/v1/organization/access-requests/{id}/approve  — approve → time-limited grant
    POST   /api/v1/organization/access-requests/{id}/reject   — reject
    DELETE /api/v1/organization/memberships/superuser     — revoke an active grant early

  Any active member — the transparency banner (6.5-f):
    GET    /api/v1/organization/superuser-access    — the org's current Superuser grant (or null)

  AUDIT_LOG_VIEW (Admin, Responder):
    GET    /api/v1/organization/audit               — the org's audit trail

Consent gate (ADR 0018): the install Superuser cannot self-grant data
access.  They file a request (api/superuser.py); an org Admin approves it
here — creating a time-limited UserOrganization row (role="superuser",
expires_at; default 24h, Admin may override or grant "until revoked") — or
rejects it.  Expiry is lazy (organization/superuser_access.py), revoke is
immediate.  Role-change discipline: the "Last Admin" invariant guard
refuses any demotion/removal that would leave the org with zero active
Admins; every governance event is audit-logged in BOTH the install audit
(organization_id=None) and the org's audit, linked via related_event_id.
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from wolf_server.audit.log import write_dual_event
from wolf_server.audit.models import AuditEvent
from wolf_server.auth.blacklist import get_session_blacklist
from wolf_server.auth.local import hash_password
from wolf_server.config import get_settings
from wolf_server.database import get_db
from wolf_server.organization.context import (
    OrganizationContext,
    require_organization_context,
)
from wolf_server.organization.models import (
    SuperuserAccessRequest,
    User,
    UserOrganization,
)
from wolf_server.organization.rbac import (
    Capability,
    ensure_not_last_admin,
    require_capability,
)
from wolf_server.organization.superuser_access import (
    active_superuser_binding,
    mark_request_ended,
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


class AccessRequestAdminResponse(BaseModel):
    """A Superuser access-request as the org's Admin sees it (6.5-f)."""

    id: uuid.UUID
    superuser_user_id: uuid.UUID
    superuser_email: str
    superuser_display_name: str
    status: str
    reason: str | None
    requested_duration_hours: int | None
    granted_expires_at: datetime | None
    requested_at: datetime
    decided_at: datetime | None
    decided_by_user_id: uuid.UUID | None
    # Display name of the deciding Admin (null while pending/cancelled, or
    # if the Admin account is gone) — for the lifecycle timeline.
    decided_by_display_name: str | None
    # When an approved grant ended (revoke/expiry); null otherwise.
    ended_at: datetime | None


class AccessApproveRequest(BaseModel):
    """How long the approved grant lasts.

    - ``requested`` (default): honour the duration the Superuser asked for
      (null requested duration → "until revoked").
    - ``hours``: grant for ``duration_hours`` (required in this mode).
    - ``until_revoked``: open-ended; only a revoke (or nothing) ends it.

    The explicit mode avoids the "is null = until-revoked or = unset?"
    ambiguity a bare nullable field would carry.
    """

    mode: Literal["requested", "hours", "until_revoked"] = "requested"
    # 1 hour .. 720 hours (30 days). Required only when mode == "hours".
    duration_hours: int | None = Field(default=None, ge=1, le=720)


class AccessRejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class SuperuserAccessResponse(BaseModel):
    """The org's current active Superuser grant — feeds the all-member
    transparency banner (6.5-f).  Endpoint returns ``null`` when none."""

    granted_by_display_name: str | None
    granted_at: datetime
    expires_at: datetime | None


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
    linked via related_event_id.  Thin wrapper over
    ``audit.log.write_dual_event`` that fills org/user/session from the
    Admin's OrganizationContext."""
    await write_dual_event(
        db,
        event_type=event_type,
        event_data=event_data,
        organization_id=ctx.organization_id,
        user_id=ctx.user_id,
        session_id=ctx.session_id,
        source_ip=source_ip,
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
                "request → approve flow, not ordinary member creation"
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
        # MSSP hygiene: don't leak internal endpoints/CLIs to a tenant Admin
        # — state the restriction only. (The operative path is the consent
        # gate's Revoke control on Settings → Access.)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The Superuser's role is fixed and can't be changed — Unauthorised.",
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
            detail="The Superuser can't be removed — revoke their access instead.",
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
            detail="The Superuser's password can't be changed — Unauthorised.",
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
        # This is an install-topology fault — log the detail for the
        # operator, but don't leak the count/CLI to a tenant Admin.
        logger.error(
            "install_superuser_misconfigured",
            active_superuser_count=len(superusers),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The Superuser account is unavailable; contact the platform operator.",
        )
    return superusers[0]


def _resolve_grant_expiry(
    body: AccessApproveRequest, requested_duration_hours: int | None
) -> datetime | None:
    """Translate an approval decision into an absolute expiry (or None for
    "until revoked").  See AccessApproveRequest for the three modes."""
    if body.mode == "until_revoked":
        return None
    if body.mode == "hours":
        if body.duration_hours is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="duration_hours is required when mode is 'hours'",
            )
        return datetime.now(UTC) + timedelta(hours=body.duration_hours)
    # mode == "requested": honour what the Superuser asked for.
    if requested_duration_hours is None:
        return None
    return datetime.now(UTC) + timedelta(hours=requested_duration_hours)


def _access_request_admin_response(
    req: SuperuserAccessRequest,
    requester: User,
    decided_by_display_name: str | None = None,
) -> AccessRequestAdminResponse:
    return AccessRequestAdminResponse(
        id=req.id,
        superuser_user_id=req.superuser_user_id,
        superuser_email=requester.email,
        superuser_display_name=requester.display_name,
        status=req.status,
        reason=req.reason,
        requested_duration_hours=req.requested_duration_hours,
        granted_expires_at=req.granted_expires_at,
        requested_at=req.requested_at,
        decided_at=req.decided_at,
        decided_by_user_id=req.decided_by_user_id,
        decided_by_display_name=decided_by_display_name,
        ended_at=req.ended_at,
    )


async def _load_org_request(
    db: AsyncSession, organization_id: uuid.UUID, request_id: uuid.UUID
) -> tuple[SuperuserAccessRequest, User]:
    """Load a request that belongs to THIS org + its requester, or 404.

    The org-scoped WHERE clause is the cross-organization guard: an Admin
    can only act on requests filed against their own organization, never
    another org's (even with a guessed request id)."""
    row = (
        await db.execute(
            select(SuperuserAccessRequest, User)
            .join(User, User.id == SuperuserAccessRequest.superuser_user_id)
            .where(
                SuperuserAccessRequest.id == request_id,
                SuperuserAccessRequest.organization_id == organization_id,
            )
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such access request in this organization",
        )
    return row[0], row[1]


async def _latest_grant_admin_name(
    db: AsyncSession, organization_id: uuid.UUID, superuser_user_id: uuid.UUID
) -> str | None:
    """Display name of the Admin who approved the current grant.

    The UserOrganization row doesn't itself record who granted it, so we
    read it from the most recent *approved* access-request.  None when not
    resolvable (e.g. a grant predating the request flow)."""
    decided_by = await db.scalar(
        select(SuperuserAccessRequest.decided_by_user_id)
        .where(
            SuperuserAccessRequest.organization_id == organization_id,
            SuperuserAccessRequest.superuser_user_id == superuser_user_id,
            SuperuserAccessRequest.status == "approved",
        )
        .order_by(SuperuserAccessRequest.decided_at.desc())
        .limit(1)
    )
    if decided_by is None:
        return None
    name: str | None = await db.scalar(select(User.display_name).where(User.id == decided_by))
    return name


@router.get("/access-requests", response_model=list[AccessRequestAdminResponse])
async def list_access_requests(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[
        OrganizationContext, Depends(require_capability(Capability.SUPERUSER_MEMBERSHIP_GRANT))
    ],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AccessRequestAdminResponse]:
    """The consent-gate inbox: Superuser access-requests for THIS org,
    pending first then newest.  Org-scoped (cross-org isolation)."""
    result = await db.execute(
        select(SuperuserAccessRequest, User)
        .join(User, User.id == SuperuserAccessRequest.superuser_user_id)
        .where(SuperuserAccessRequest.organization_id == ctx.organization_id)
        .order_by(
            case((SuperuserAccessRequest.status == "pending", 0), else_=1),
            SuperuserAccessRequest.requested_at.desc(),
        )
        .limit(limit)
    )
    rows = result.all()

    # Resolve the deciding Admins' display names in one query (for the
    # lifecycle timeline) rather than N per-row lookups.
    decider_ids = {req.decided_by_user_id for req, _ in rows if req.decided_by_user_id}
    decider_names: dict[uuid.UUID, str] = {}
    if decider_ids:
        decider_names = {
            uid: name
            for uid, name in (
                await db.execute(
                    select(User.id, User.display_name).where(User.id.in_(decider_ids))
                )
            ).all()
        }

    return [
        _access_request_admin_response(
            req,
            requester,
            decider_names.get(req.decided_by_user_id) if req.decided_by_user_id else None,
        )
        for req, requester in rows
    ]


@router.post(
    "/access-requests/{request_id}/approve",
    response_model=AccessRequestAdminResponse,
)
async def approve_access_request(
    request_id: uuid.UUID,
    body: AccessApproveRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[
        OrganizationContext, Depends(require_capability(Capability.SUPERUSER_MEMBERSHIP_GRANT))
    ],
) -> AccessRequestAdminResponse:
    """Approve a pending request → create the time-limited Superuser grant.

    The consent gate (ADR 0018): only an org Admin can extend the
    Superuser's reach into this org's data.  The Admin may honour the
    requested duration, override it, or grant "until revoked"; expiry is
    enforced lazily (organization/superuser_access.py).  Audited in both
    the org and install audit logs."""
    req, requester = await _load_org_request(db, ctx.organization_id, request_id)
    if req.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Request is already {req.status}; only a pending request can be approved",
        )
    if await active_superuser_binding(db, ctx.organization_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The Superuser already has active access to this organization",
        )

    expires_at = _resolve_grant_expiry(body, req.requested_duration_hours)
    now = datetime.now(UTC)
    db.add(
        UserOrganization(
            id=uuid.uuid4(),
            user_id=req.superuser_user_id,
            organization_id=ctx.organization_id,
            role="superuser",
            created_at=now,
            expires_at=expires_at,
        )
    )
    req.status = "approved"
    req.decided_at = now
    req.decided_by_user_id = ctx.user_id
    req.granted_expires_at = expires_at
    await db.flush()

    await _write_dual_audit(
        db,
        ctx,
        event_type="organization.superuser_membership.granted",
        event_data={
            "superuser_user_id": str(req.superuser_user_id),
            "granted_by": str(ctx.user_id),
            "request_id": str(req.id),
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
        source_ip=_source_ip(request),
    )
    await db.commit()
    logger.info(
        "superuser_membership_granted",
        organization_id=str(ctx.organization_id),
        granted_by=str(ctx.user_id),
        request_id=str(req.id),
        expires_at=expires_at.isoformat() if expires_at else None,
    )
    admin_name: str | None = await db.scalar(
        select(User.display_name).where(User.id == ctx.user_id)
    )
    return _access_request_admin_response(req, requester, admin_name)


@router.post(
    "/access-requests/{request_id}/reject",
    response_model=AccessRequestAdminResponse,
)
async def reject_access_request(
    request_id: uuid.UUID,
    body: AccessRejectRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[
        OrganizationContext, Depends(require_capability(Capability.SUPERUSER_MEMBERSHIP_GRANT))
    ],
) -> AccessRequestAdminResponse:
    """Reject a pending request — no grant is created."""
    req, requester = await _load_org_request(db, ctx.organization_id, request_id)
    if req.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Request is already {req.status}; only a pending request can be rejected",
        )
    now = datetime.now(UTC)
    req.status = "rejected"
    req.decided_at = now
    req.decided_by_user_id = ctx.user_id
    await db.flush()

    await _write_dual_audit(
        db,
        ctx,
        event_type="organization.superuser_access.rejected",
        event_data={
            "superuser_user_id": str(req.superuser_user_id),
            "rejected_by": str(ctx.user_id),
            "request_id": str(req.id),
            "reason": body.reason,
        },
        source_ip=_source_ip(request),
    )
    await db.commit()
    admin_name: str | None = await db.scalar(
        select(User.display_name).where(User.id == ctx.user_id)
    )
    return _access_request_admin_response(req, requester, admin_name)


@router.get("/superuser-access", response_model=SuperuserAccessResponse | None)
async def get_superuser_access(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[OrganizationContext, Depends(require_organization_context)],
) -> SuperuserAccessResponse | None:
    """The org's current active Superuser grant, or null — feeds the
    all-member transparency banner (6.5-f).

    Readable by EVERY active member of the org (only require_organization_
    context, not a capability): the whole point is that everyone can see
    when an install operator has access.  Runs lazy expiry so the banner
    self-clears the moment a grant lapses."""
    binding = await active_superuser_binding(db, ctx.organization_id)
    # active_superuser_binding may have pruned an expired row (delete +
    # audit); commit so that lands regardless of the outcome.
    await db.commit()
    if binding is None:
        return None
    granted_by = await _latest_grant_admin_name(db, ctx.organization_id, binding.user_id)
    return SuperuserAccessResponse(
        granted_by_display_name=granted_by,
        granted_at=binding.created_at,
        expires_at=binding.expires_at,
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
    await mark_request_ended(
        db, ctx.organization_id, superuser.id, "revoked", datetime.now(UTC)
    )

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
