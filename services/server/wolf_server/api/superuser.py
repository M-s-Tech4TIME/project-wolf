"""Superuser-only API routes — Phase 6.5-a, ADR 0018.

POST /api/v1/users/{user_id}/password-reset
    Superuser resets any user's password (recovery mechanism). A fresh
    random password is generated server-side and returned ONCE in the
    response for out-of-band delivery; an audit event captures the
    Superuser identity + target user + timestamp. All the target's
    existing sessions are blacklisted (6.5-g).

POST /api/v1/users/{user_id}/sessions/revoke
    Force-revoke (6.5-g): blacklist every outstanding session for a
    user — the compromised-account response. The account itself stays
    active; the user re-authenticates with their existing password.

POST /api/v1/organizations/{organization_id}/recovery/admin
    Break-glass org-recovery per ADR 0018 §"Break-glass / org-recovery":
    when an organization has ZERO active Admins, the Superuser creates a
    new Admin and force-adds them to the org. Refused (409) while any
    active Admin exists — this flow restores Admin succession, it never
    bypasses it. The Superuser still gains NO data access (no
    UserOrganization row for the Superuser is created).

GET /api/v1/superuser/audit
    Install-wide audit trail (Phase 6.5-d) — every organization's events
    plus system-level rows (organization_id IS NULL), newest first,
    paginated. Distinct from the per-org GET /api/v1/organization/audit,
    which is org-scoped and excludes system-level rows. Each row carries
    its organization's name (null for system-level events). "Install-wide"
    is the VIEW scope (the whole installation); "system-level" is the
    org-less row attribution (matches the AuditEvent model's own wording).

Authorization: all routes require an authenticated session whose user
has ``is_superuser=True`` (the bootstrap "Wolf" account). The richer
role-decorator pattern arrives with 6.5-b; this dependency is the
Superuser-specific primitive it will build on.
"""

import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wolf_server.audit.log import write_event
from wolf_server.audit.models import AuditEvent
from wolf_server.auth.blacklist import get_session_blacklist
from wolf_server.auth.local import hash_password
from wolf_server.config import get_settings
from wolf_server.database import get_db
from wolf_server.organization.models import Organization, User, UserOrganization

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["superuser"])

# Matches the bootstrap CLI's entropy (24 bytes -> 32 url-safe chars).
_PASSWORD_BYTES = 24


async def _revoke_all_sessions(user_id: uuid.UUID) -> None:
    """Watermark-revoke every outstanding session for a user (6.5-g).

    TTL covers the access-token lifetime — the longest any outstanding
    token can still authenticate. (No refresh endpoint exists yet; when
    one lands it must check the same watermark and this TTL must grow to
    the refresh lifetime.)
    """
    ttl = get_settings().access_token_expire_minutes * 60
    await get_session_blacklist().revoke_user(str(user_id), ttl_seconds=ttl)


# ── Dependency ───────────────────────────────────────────────────────────────


async def require_superuser(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """FastAPI dependency: the session user must be the active Superuser.

    Unlike require_organization_context this carries NO organization
    scope — the Superuser is an install-level identity with zero org
    memberships by default (ADR 0018 org-consent gate).
    """
    session: dict[str, Any] = getattr(request.state, "session", {})
    user_id_raw = session.get("user_id")
    if not user_id_raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    try:
        user_id = uuid.UUID(str(user_id_raw))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session"
        ) from None

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active or not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superuser privileges required",
        )
    return user


# ── Schemas ──────────────────────────────────────────────────────────────────


class PasswordResetResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    # Returned exactly once for out-of-band delivery to the affected
    # user; never logged or persisted in plaintext.
    new_password: str


class RecoveryAdminRequest(BaseModel):
    email: EmailStr
    display_name: str = "Organization Admin"


class RecoveryAdminResponse(BaseModel):
    organization_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    role: str
    # Present only when the recovery created a brand-new user account;
    # None when an existing account was force-added as Admin.
    new_password: str | None


class InstallAuditEventResponse(BaseModel):
    id: uuid.UUID
    event_type: str
    event_data: dict[str, Any] | None
    # organization_id + organization_name are None for system-level
    # events (startup, health checks, org-less auth, etc.) — the
    # AuditEvent model calls these "system-level"; the UI badges them
    # "System".
    organization_id: uuid.UUID | None
    organization_name: str | None
    user_id: uuid.UUID | None
    source_ip: str | None
    related_event_id: uuid.UUID | None
    created_at: datetime


class InstallAuditPageResponse(BaseModel):
    events: list[InstallAuditEventResponse]
    limit: int
    offset: int


# ── Routes ───────────────────────────────────────────────────────────────────


@router.post("/users/{user_id}/password-reset", response_model=PasswordResetResponse)
async def reset_user_password(
    user_id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    superuser: Annotated[User, Depends(require_superuser)],
) -> PasswordResetResponse:
    """Superuser resets any user's password (audit-emitted)."""
    target = await db.scalar(select(User).where(User.id == user_id))
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"User {user_id} not found"
        )
    if target.is_superuser:
        # The Superuser's own recovery path is the bootstrap_superuser
        # wrapper (operator-on-host) — never the API, so a hijacked
        # Superuser session cannot rotate its own credential silently.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Superuser password is rotated via the bootstrap_superuser "
                "CLI on the host, not via the API"
            ),
        )

    new_password = secrets.token_urlsafe(_PASSWORD_BYTES)
    target.hashed_password = hash_password(new_password)
    target.updated_at = datetime.now(UTC)

    # ADR 0018 Round 1: a password reset invalidates ALL the target's
    # existing sessions — whoever prompted the reset (lost credential,
    # suspected compromise), live sessions must not outlive it.
    await _revoke_all_sessions(target.id)

    await write_event(
        db,
        event_type="superuser.user_password.reset",
        event_data={
            "target_user_id": str(target.id),
            "target_email": target.email,
            "sessions_revoked": True,
        },
        user_id=superuser.id,
        session_id=str(getattr(request.state, "session", {}).get("session_id", "")),
        source_ip=request.client.host if request.client else None,
    )
    await db.commit()

    logger.info(
        "superuser_password_reset",
        superuser_id=str(superuser.id),
        target_user_id=str(target.id),
    )
    return PasswordResetResponse(user_id=target.id, email=target.email, new_password=new_password)


@router.post("/users/{user_id}/sessions/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_user_sessions(
    user_id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    superuser: Annotated[User, Depends(require_superuser)],
) -> None:
    """Force-revoke every outstanding session for a user (audit-emitted).

    Unlike password-reset this is allowed against ANY account, including
    the Superuser's own — it only forces re-authentication, it never
    touches credentials.
    """
    target = await db.scalar(select(User).where(User.id == user_id))
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"User {user_id} not found"
        )

    await _revoke_all_sessions(target.id)

    await write_event(
        db,
        event_type="superuser.user_sessions.revoked",
        event_data={"target_user_id": str(target.id), "target_email": target.email},
        user_id=superuser.id,
        session_id=str(getattr(request.state, "session", {}).get("session_id", "")),
        source_ip=request.client.host if request.client else None,
    )
    await db.commit()

    logger.info(
        "superuser_sessions_revoked",
        superuser_id=str(superuser.id),
        target_user_id=str(target.id),
    )


@router.post(
    "/organizations/{organization_id}/recovery/admin",
    response_model=RecoveryAdminResponse,
    status_code=status.HTTP_201_CREATED,
)
async def recover_organization_admin(
    organization_id: uuid.UUID,
    body: RecoveryAdminRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    superuser: Annotated[User, Depends(require_superuser)],
) -> RecoveryAdminResponse:
    """Break-glass: force-add a new Admin to an organization with zero Admins."""
    organization = await db.scalar(
        select(Organization).where(
            Organization.id == organization_id, Organization.is_active.is_(True)
        )
    )
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization {organization_id} not found",
        )

    # The gate: refuse while ANY active Admin exists. Recovery restores
    # Admin succession; it must never be usable as a bypass around a
    # living Admin (ADR 0018: "the only way Superuser authority extends
    # into an Adminless org").
    admin_rows = await db.execute(
        select(UserOrganization)
        .join(User, User.id == UserOrganization.user_id)
        .where(
            UserOrganization.organization_id == organization_id,
            UserOrganization.role == "admin",
            User.is_active.is_(True),
        )
    )
    if admin_rows.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Organization still has an active Admin — recovery is only "
                "for organizations with zero Admins. Use the normal "
                "user-management flow instead."
            ),
        )

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
        # The org-consent gate: the Superuser cannot use recovery to
        # hand themselves a membership.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The Superuser cannot be added to an organization via recovery",
        )

    binding = await db.scalar(
        select(UserOrganization).where(
            UserOrganization.user_id == user.id,
            UserOrganization.organization_id == organization_id,
        )
    )
    if binding is not None:
        binding.role = "admin"
    else:
        db.add(
            UserOrganization(
                id=uuid.uuid4(),
                user_id=user.id,
                organization_id=organization_id,
                role="admin",
                created_at=now,
            )
        )

    # organization_id is set so the event lands in the org's own audit
    # view — ADR 0018: "recovery flow used" must be visible to all org
    # members, not just the install-level log.
    await write_event(
        db,
        event_type="organization.recovery.admin_added",
        event_data={
            "recovery_flow": True,
            "admin_user_id": str(user.id),
            "admin_email": user.email,
            "created_new_user": new_password is not None,
        },
        organization_id=organization_id,
        user_id=superuser.id,
        session_id=str(getattr(request.state, "session", {}).get("session_id", "")),
        source_ip=request.client.host if request.client else None,
    )
    await db.commit()

    logger.info(
        "organization_recovery_admin_added",
        superuser_id=str(superuser.id),
        organization_id=str(organization_id),
        admin_user_id=str(user.id),
    )
    return RecoveryAdminResponse(
        organization_id=organization_id,
        user_id=user.id,
        email=user.email,
        role="admin",
        new_password=new_password,
    )


@router.get("/superuser/audit", response_model=InstallAuditPageResponse)
async def view_install_audit(
    db: Annotated[AsyncSession, Depends(get_db)],
    superuser: Annotated[User, Depends(require_superuser)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> InstallAuditPageResponse:
    """Install-wide audit trail, newest first (Superuser-only).

    Unlike the org view (GET /api/v1/organization/audit) this is not
    scoped to one organization and does NOT exclude system-level rows
    (organization_id IS NULL). A LEFT JOIN carries each row's
    organization name (None for system-level events). Mirrors the
    org-audit query/pagination shape in org_management.view_audit_log.
    """
    result = await db.execute(
        select(AuditEvent, Organization.name)
        .outerjoin(Organization, Organization.id == AuditEvent.organization_id)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id)
        .limit(limit)
        .offset(offset)
    )
    events = [
        InstallAuditEventResponse(
            id=event.id,
            event_type=event.event_type,
            event_data=dict(event.event_data) if event.event_data is not None else None,
            organization_id=event.organization_id,
            organization_name=org_name,
            user_id=event.user_id,
            source_ip=event.source_ip,
            related_event_id=event.related_event_id,
            created_at=event.created_at,
        )
        for event, org_name in result.all()
    ]
    return InstallAuditPageResponse(events=events, limit=limit, offset=offset)
