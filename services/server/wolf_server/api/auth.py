"""Authentication API routes.

POST /api/v1/auth/login          — local account login (three-shape response, ADR 0018)
POST /api/v1/auth/logout         — blacklist session + clear cookies
GET  /api/v1/auth/me             — current user info; honors X-Organization-Id (requires auth)
GET  /api/v1/auth/me/organizations     — list organizations the user belongs to (requires auth)
POST /api/v1/auth/select-organization  — record post-login org choice for audit (optional)
POST /api/v1/auth/switch-organization  — record per-tab org switch for audit (optional)
GET  /api/v1/auth/oidc/start     — redirect to OIDC IdP (when configured)
GET  /api/v1/auth/oidc/callback  — OIDC code exchange (when configured)
"""

import time
import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from wolf_server.audit.log import write_event
from wolf_server.auth.blacklist import get_session_blacklist
from wolf_server.auth.local import create_access_token, create_refresh_token, verify_password
from wolf_server.auth.middleware import COOKIE_NAME
from wolf_server.auth.oidc import get_authorization_url, oidc_is_configured
from wolf_server.bootstrap.superuser import SUPERUSER_EMAIL, SUPERUSER_USERNAME
from wolf_server.config import get_settings
from wolf_server.database import get_db
from wolf_server.organization.context import ORG_HEADER
from wolf_server.organization.models import Organization, User, UserOrganization

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
_settings = get_settings()


# ── Request / response schemas ───────────────────────────────────────────────


class LoginRequest(BaseModel):
    # Plain str (not EmailStr) so the fixed Superuser username "Wolf"
    # is accepted alongside regular email addresses (ADR 0018: the
    # Superuser logs in by username). Non-email strings simply fail the
    # user lookup and 401 like any wrong credential.
    email: str
    password: str
    # TRANSITIONAL (removed with 6.5-c-ii): the pre-header dashboard sends
    # the org at login. The new flow authenticates org-less and carries
    # the org per request in the X-Organization-Id header instead.
    organization_id: uuid.UUID | None = None


class MembershipInfo(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    role: str


class LoginResponse(BaseModel):
    """Three-shape login response (ADR 0018 §login UX, Phase 6.5-c-i).

    Exactly one of these applies:
      - is_superuser=True (+ redirect): install-admin session, no org scope
      - auto_selected_organization set: single membership, ready for /chat
      - needs_org_selection=True (+ memberships): the dashboard renders the
        org picker; the session cookie is already issued (auth-only)
    The flat organization_id/role fields mirror the legacy shape and stay
    populated whenever an org is resolved (removed with 6.5-c-ii).
    """

    user_id: uuid.UUID
    email: str
    display_name: str
    organization_id: uuid.UUID | None
    # None while no org is resolved (needs_org_selection).
    role: str | None
    is_superuser: bool = False
    # Client-side landing route hint (Superuser → install-admin UI).
    redirect: str | None = None
    auto_selected_organization: MembershipInfo | None = None
    needs_org_selection: bool = False
    memberships: list[MembershipInfo] | None = None


class MeResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    organization_id: uuid.UUID | None
    role: str


# ── Routes ───────────────────────────────────────────────────────────────────


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LoginResponse:
    """Authenticate with email + password and receive a session cookie."""
    source_ip = request.client.host if request.client else None

    # Load the user by email. The fixed Superuser username "Wolf" maps
    # to the reserved internal address (ADR 0018 — operators type the
    # username, the account is keyed by email).
    lookup_email = SUPERUSER_EMAIL if body.email == SUPERUSER_USERNAME else body.email
    result = await db.execute(select(User).where(User.email == lookup_email))
    user = result.scalar_one_or_none()

    # Constant-time password check to mitigate user enumeration.
    if user is None or user.hashed_password is None:
        # Verify a dummy hash to prevent timing attacks.
        _dummy_verify()
        await _audit_login_failure(db, body.email, source_ip, "user_not_found")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )

    if not verify_password(body.password, user.hashed_password):
        await _audit_login_failure(db, body.email, source_ip, "wrong_password", user_id=user.id)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )

    if not user.is_active:
        await _audit_login_failure(db, body.email, source_ip, "user_inactive", user_id=user.id)
        await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is inactive")

    # Resolve the response shape (ADR 0018 §login UX, Phase 6.5-c-i).
    is_superuser = False
    redirect: str | None = None
    auto_selected: MembershipInfo | None = None
    needs_org_selection = False
    memberships: list[MembershipInfo] | None = None

    if user.is_superuser:
        # The install-level Superuser authenticates with NO organization
        # context — zero memberships by default (ADR 0018 org-consent
        # gate). The org-selector is never shown; the install-admin UI
        # is the landing surface.
        selected_organization_id: uuid.UUID | None = None
        role: str | None = "superuser"
        is_superuser = True
        redirect = "/superuser/dashboard"
    else:
        # Resolve organization memberships — active orgs only (a binding
        # to a soft-deleted org must not be selectable or listed).
        bindings_result = await db.execute(
            select(UserOrganization)
            .join(Organization, Organization.id == UserOrganization.organization_id)
            .where(UserOrganization.user_id == user.id, Organization.is_active.is_(True))
            .options(selectinload(UserOrganization.organization))
        )
        bindings = bindings_result.scalars().all()

        if not bindings:
            # 401 (not 403) per ADR: the credential was right but there is
            # nothing to log in TO; the friendlier detail tells the user
            # who can fix it.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "User has no organization memberships. If this is "
                    "unexpected, contact your organization admin."
                ),
            )

        def _info(b: UserOrganization) -> MembershipInfo:
            return MembershipInfo(
                organization_id=b.organization_id,
                organization_name=b.organization.name,
                role=b.role,
            )

        if body.organization_id is not None:
            # TRANSITIONAL legacy path (pre-6.5-c-ii dashboard sends the
            # org at login). Removed together with the header fallback.
            binding = next((b for b in bindings if b.organization_id == body.organization_id), None)
            if binding is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User is not a member of the requested organization",
                )
            selected_organization_id = binding.organization_id
            role = binding.role
        elif len(bindings) == 1:
            binding = bindings[0]
            selected_organization_id = binding.organization_id
            role = binding.role
            auto_selected = _info(binding)
        else:
            # N>1 memberships: authenticate now (auth-only cookie), let the
            # dashboard render the org picker. Org context arrives per
            # request via the X-Organization-Id header afterwards.
            selected_organization_id = None
            role = None
            needs_org_selection = True
            memberships = [_info(b) for b in bindings]

    session_id = str(uuid.uuid4())
    access_token = create_access_token(user.id, selected_organization_id, role or "", session_id)
    refresh_token = create_refresh_token(user.id, session_id)

    # Write audit event BEFORE setting the cookie so the event is in-tx.
    await write_event(
        db,
        event_type="auth.login.success",
        event_data={
            "email": user.email,
            "method": "local",
            "needs_org_selection": needs_org_selection,
        },
        organization_id=selected_organization_id,
        user_id=user.id,
        session_id=session_id,
        source_ip=source_ip,
    )
    await db.commit()

    _set_auth_cookies(response, access_token, refresh_token)

    logger.info(
        "login_success",
        user_id=str(user.id),
        organization_id=str(selected_organization_id),
        needs_org_selection=needs_org_selection,
    )

    return LoginResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        organization_id=selected_organization_id,
        role=role,
        is_superuser=is_superuser,
        redirect=redirect,
        auto_selected_organization=auto_selected,
        needs_org_selection=needs_org_selection,
        memberships=memberships,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Blacklist the session server-side, clear the cookies, audit the logout.

    Phase 6.5-g: cookie deletion alone never invalidates the JWT — a copied
    token would keep working until expiry. The blacklist entry (TTL = the
    token's remaining lifetime) makes the logout effective in every tab
    immediately.
    """
    session: dict[str, Any] = getattr(request.state, "session", {})
    user_id_raw = session.get("user_id")
    organization_id_raw = session.get("organization_id")
    session_id = str(session.get("session_id", ""))

    if session_id:
        exp_raw = session.get("exp")
        remaining = (
            int(float(str(exp_raw)) - time.time())
            if exp_raw
            else _settings.access_token_expire_minutes * 60
        )
        await get_session_blacklist().revoke_session(session_id, ttl_seconds=max(remaining, 1))

    if user_id_raw:
        try:
            await write_event(
                db,
                event_type="auth.logout",
                event_data=None,
                # None for the org-less Superuser session — the logout
                # still lands in the install-level audit log.
                organization_id=(
                    uuid.UUID(str(organization_id_raw)) if organization_id_raw else None
                ),
                user_id=uuid.UUID(str(user_id_raw)),
                session_id=session_id,
                source_ip=request.client.host if request.client else None,
            )
            await db.commit()
        except Exception:  # noqa: BLE001, S110
            pass  # Never block logout due to audit failure.

    response.delete_cookie(COOKIE_NAME)
    response.delete_cookie("wolf_refresh_token")


@router.get("/me", response_model=MeResponse)
async def me(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MeResponse:
    """Return current session's user and organization info.

    The JWT only carries user_id/organization_id/role; email and display_name
    come from the User row. Surfacing them in the sidebar profile chip
    (Slice 5.0c-b) was the original prompt for wiring this up.
    """
    session: dict[str, Any] = getattr(request.state, "session", {})
    if not session.get("user_id"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user_id = uuid.UUID(str(session["user_id"]))
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        # Session points to a user that no longer exists — treat as logged out.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Stale session")

    # Per-tab org context (6.5-c-i): when the X-Organization-Id header is
    # present, org + role reflect THAT membership, so each tab's profile
    # chip shows the org the tab is actually working in. Header absent →
    # the JWT claim (transitional fallback, removed with 6.5-c-ii).
    header_raw = request.headers.get(ORG_HEADER)
    if header_raw is not None:
        try:
            header_org_id = uuid.UUID(header_raw.strip())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid {ORG_HEADER} header: not a UUID",
            ) from None
        binding = await db.scalar(
            select(UserOrganization).where(
                UserOrganization.user_id == user_id,
                UserOrganization.organization_id == header_org_id,
            )
        )
        if binding is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of this organization",
            )
        return MeResponse(
            user_id=user_id,
            email=user.email or "",
            display_name=user.display_name or "",
            organization_id=header_org_id,
            role=binding.role,
        )

    organization_id_raw = session.get("organization_id")
    return MeResponse(
        user_id=user_id,
        email=user.email or "",
        display_name=user.display_name or "",
        organization_id=uuid.UUID(str(organization_id_raw)) if organization_id_raw else None,
        role=str(session.get("role", "")),
    )


class OrganizationMembership(BaseModel):
    """One organization the current user is a member of."""

    id: uuid.UUID
    slug: str
    name: str
    role: str


@router.get("/me/organizations", response_model=list[OrganizationMembership])
async def my_organizations(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[OrganizationMembership]:
    """List all organizations the current user is a member of.

    Used by wolf-dashboard's organization switcher.  Only returns active organizations;
    the user's per-organization role comes from the user_organizations binding.
    """
    session: dict[str, Any] = getattr(request.state, "session", {})
    user_id_raw = session.get("user_id")
    if not user_id_raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user_id = uuid.UUID(str(user_id_raw))

    rows = await db.execute(
        select(UserOrganization, Organization)
        .join(Organization, Organization.id == UserOrganization.organization_id)
        .where(UserOrganization.user_id == user_id, Organization.is_active.is_(True))
        .order_by(Organization.slug)
    )
    return [
        OrganizationMembership(
            id=organization.id, slug=organization.slug, name=organization.name, role=binding.role
        )
        for binding, organization in rows.all()
    ]


class OrganizationSelectRequest(BaseModel):
    organization_id: uuid.UUID


async def _record_org_choice(
    event_type: str,
    body: OrganizationSelectRequest,
    request: Request,
    db: AsyncSession,
) -> MembershipInfo:
    """Shared core of select-organization / switch-organization (6.5-c-i).

    These endpoints are OPTIONAL per ADR 0018 — org-context routing happens
    via the X-Organization-Id header, not here. They exist purely so the
    choice lands in the audit trail (who worked in which org when) and can
    back last-used persistence later. Membership is validated so the audit
    log can never record a selection the user could not actually make.
    """
    session: dict[str, Any] = getattr(request.state, "session", {})
    user_id_raw = session.get("user_id")
    if not user_id_raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user_id = uuid.UUID(str(user_id_raw))

    binding = await db.scalar(
        select(UserOrganization)
        .join(Organization, Organization.id == UserOrganization.organization_id)
        .where(
            UserOrganization.user_id == user_id,
            UserOrganization.organization_id == body.organization_id,
            Organization.is_active.is_(True),
        )
        .options(selectinload(UserOrganization.organization))
    )
    if binding is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this organization",
        )

    await write_event(
        db,
        event_type=event_type,
        event_data={"role": binding.role},
        organization_id=body.organization_id,
        user_id=user_id,
        session_id=str(session.get("session_id", "")),
        source_ip=request.client.host if request.client else None,
    )
    await db.commit()

    return MembershipInfo(
        organization_id=binding.organization_id,
        organization_name=binding.organization.name,
        role=binding.role,
    )


@router.post("/select-organization", response_model=MembershipInfo)
async def select_organization(
    body: OrganizationSelectRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MembershipInfo:
    """Record the post-login org selection (audit; ADR 0018, optional)."""
    return await _record_org_choice("auth.organization.selected", body, request, db)


@router.post("/switch-organization", response_model=MembershipInfo)
async def switch_organization(
    body: OrganizationSelectRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MembershipInfo:
    """Record a mid-session per-tab org switch (audit; ADR 0018, optional)."""
    return await _record_org_choice("auth.organization.switched", body, request, db)


@router.get("/oidc/start")
async def oidc_start(redirect_uri: str = "") -> dict[str, str]:
    """Redirect to the configured OIDC IdP."""
    if not oidc_is_configured():
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="OIDC is not configured on this instance.",
        )
    state = str(uuid.uuid4())
    url = get_authorization_url(redirect_uri, state)
    return {"authorization_url": url, "state": state}


# ── Internal helpers ─────────────────────────────────────────────────────────


def _dummy_verify() -> None:
    """Perform a no-op bcrypt verify to maintain constant-time behaviour."""
    import bcrypt  # noqa: PLC0415

    bcrypt.checkpw(b"dummy", bcrypt.hashpw(b"dummy", bcrypt.gensalt()))


async def _audit_login_failure(
    db: AsyncSession,
    email: str,
    source_ip: str | None,
    reason: str,
    user_id: uuid.UUID | None = None,
) -> None:
    await write_event(
        db,
        event_type="auth.login.failure",
        event_data={"email": email, "reason": reason},
        user_id=user_id,
        source_ip=source_ip,
    )


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    is_prod = not _settings.is_development and not _settings.is_test
    response.set_cookie(
        key=COOKIE_NAME,
        value=access_token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        max_age=_settings.access_token_expire_minutes * 60,
    )
    response.set_cookie(
        key="wolf_refresh_token",
        value=refresh_token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        max_age=_settings.refresh_token_expire_days * 86400,
    )
