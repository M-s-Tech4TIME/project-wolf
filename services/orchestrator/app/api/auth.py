"""Authentication API routes.

POST /api/v1/auth/login          — local account login
POST /api/v1/auth/logout         — clear session cookie
GET  /api/v1/auth/me             — return current user info (requires auth)
GET  /api/v1/auth/me/tenants     — list tenants the user belongs to (requires auth)
GET  /api/v1/auth/oidc/start     — redirect to OIDC IdP (when configured)
GET  /api/v1/auth/oidc/callback  — OIDC code exchange (when configured)
"""

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_event
from app.auth.local import create_access_token, create_refresh_token, verify_password
from app.auth.middleware import COOKIE_NAME
from app.auth.oidc import get_authorization_url, oidc_is_configured
from app.config import get_settings
from app.database import get_db
from app.tenancy.models import Tenant, User, UserTenant

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
_settings = get_settings()


# ── Request / response schemas ───────────────────────────────────────────────


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    # The tenant the user wants to operate in.  If omitted and the user
    # belongs to exactly one tenant, that tenant is used automatically.
    tenant_id: uuid.UUID | None = None


class LoginResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    tenant_id: uuid.UUID
    role: str


class MeResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    tenant_id: uuid.UUID
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

    # Load the user by email.
    result = await db.execute(select(User).where(User.email == body.email))
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is inactive"
        )

    # Resolve tenant membership.
    tenant_query = select(UserTenant).where(UserTenant.user_id == user.id)
    bindings_result = await db.execute(tenant_query)
    bindings = bindings_result.scalars().all()

    if not bindings:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User has no tenant memberships",
        )

    if body.tenant_id is not None:
        binding = next((b for b in bindings if b.tenant_id == body.tenant_id), None)
        if binding is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not a member of the requested tenant",
            )
    elif len(bindings) == 1:
        binding = bindings[0]
    else:
        tenant_ids = [str(b.tenant_id) for b in bindings]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User belongs to multiple tenants; specify tenant_id. Options: {tenant_ids}",
        )

    session_id = str(uuid.uuid4())
    access_token = create_access_token(user.id, binding.tenant_id, binding.role, session_id)
    refresh_token = create_refresh_token(user.id, session_id)

    # Write audit event BEFORE setting the cookie so the event is in-tx.
    await write_event(
        db,
        event_type="auth.login.success",
        event_data={
            "email": user.email,
            "method": "local",
        },
        tenant_id=binding.tenant_id,
        user_id=user.id,
        session_id=session_id,
        source_ip=source_ip,
    )
    await db.commit()

    _set_auth_cookies(response, access_token, refresh_token)

    logger.info("login_success", user_id=str(user.id), tenant_id=str(binding.tenant_id))

    return LoginResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        tenant_id=binding.tenant_id,
        role=binding.role,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Clear the session cookies.  Audit the logout if a valid session is present."""
    session: dict[str, Any] = getattr(request.state, "session", {})
    user_id_raw = session.get("user_id")
    tenant_id_raw = session.get("tenant_id")
    session_id = str(session.get("session_id", ""))

    if user_id_raw and tenant_id_raw:
        try:
            await write_event(
                db,
                event_type="auth.logout",
                event_data=None,
                tenant_id=uuid.UUID(str(tenant_id_raw)),
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
    """Return current session's user and tenant info.

    The JWT only carries user_id/tenant_id/role; email and display_name
    come from the User row. Surfacing them in the sidebar profile chip
    (Slice 5.0c-b) was the original prompt for wiring this up.
    """
    session: dict[str, Any] = getattr(request.state, "session", {})
    if not session.get("user_id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    user_id = uuid.UUID(str(session["user_id"]))
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        # Session points to a user that no longer exists — treat as logged out.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Stale session"
        )
    return MeResponse(
        user_id=user_id,
        email=user.email or "",
        display_name=user.display_name or "",
        tenant_id=uuid.UUID(str(session["tenant_id"])),
        role=str(session.get("role", "")),
    )


class TenantMembership(BaseModel):
    """One tenant the current user is a member of."""

    id: uuid.UUID
    slug: str
    name: str
    role: str


@router.get("/me/tenants", response_model=list[TenantMembership])
async def my_tenants(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[TenantMembership]:
    """List all tenants the current user is a member of.

    Used by the frontend's tenant switcher.  Only returns active tenants;
    the user's per-tenant role comes from the user_tenants binding.
    """
    session: dict[str, Any] = getattr(request.state, "session", {})
    user_id_raw = session.get("user_id")
    if not user_id_raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    user_id = uuid.UUID(str(user_id_raw))

    rows = await db.execute(
        select(UserTenant, Tenant)
        .join(Tenant, Tenant.id == UserTenant.tenant_id)
        .where(UserTenant.user_id == user_id, Tenant.is_active.is_(True))
        .order_by(Tenant.slug)
    )
    return [
        TenantMembership(
            id=tenant.id, slug=tenant.slug, name=tenant.name, role=binding.role
        )
        for binding, tenant in rows.all()
    ]


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
