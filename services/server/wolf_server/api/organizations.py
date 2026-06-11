"""Organization CRUD — Superuser-only, install-level (Phase 6.5-b, ADR 0018).

Capability-matrix row: "Create / delete orgs — Superuser".  These routes are
install-scoped (no organization context — the Superuser session carries
organization_id=None), so they gate on require_superuser rather than the
org-scoped require_capability pattern.

Deletion is a soft-delete (is_active=False): the org's audit trail, users,
and knowledge rows stay intact for forensics, and every org-scoped lookup
in the stack already filters on is_active.  6.5-d builds the Organizations
dashboard UI on these endpoints.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wolf_server.api.superuser import require_superuser
from wolf_server.audit.log import write_event
from wolf_server.database import get_db
from wolf_server.organization.models import Organization, User

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/organizations", tags=["organizations"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class OrganizationCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # The slug is the immutable isolation key (secrets backend, audit,
    # logs all key on it) — created once, never editable via the API.
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


class OrganizationUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class OrganizationResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    created_at: datetime


def _to_response(org: Organization) -> OrganizationResponse:
    return OrganizationResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        is_active=org.is_active,
        created_at=org.created_at,
    )


def _session_id(request: Request) -> str:
    return str(getattr(request.state, "session", {}).get("session_id", ""))


def _source_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# ── Routes ───────────────────────────────────────────────────────────────────


@router.get("", response_model=list[OrganizationResponse])
async def list_organizations(
    db: Annotated[AsyncSession, Depends(get_db)],
    superuser: Annotated[User, Depends(require_superuser)],
) -> list[OrganizationResponse]:
    """List every organization, active and soft-deleted alike."""
    result = await db.execute(select(Organization).order_by(Organization.created_at))
    return [_to_response(org) for org in result.scalars()]


@router.post("", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
async def create_organization(
    body: OrganizationCreateRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    superuser: Annotated[User, Depends(require_superuser)],
) -> OrganizationResponse:
    """Create an organization (Superuser-only, audit-emitted)."""
    existing = await db.scalar(select(Organization).where(Organization.slug == body.slug))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An organization with slug {body.slug!r} already exists",
        )

    now = datetime.now(UTC)
    org = Organization(
        id=uuid.uuid4(),
        name=body.name,
        slug=body.slug,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(org)
    await db.flush()

    await write_event(
        db,
        event_type="organization.created",
        event_data={"name": org.name, "slug": org.slug},
        organization_id=org.id,
        user_id=superuser.id,
        session_id=_session_id(request),
        source_ip=_source_ip(request),
    )
    await db.commit()

    logger.info("organization_created", organization_id=str(org.id), slug=org.slug)
    return _to_response(org)


@router.patch("/{organization_id}", response_model=OrganizationResponse)
async def update_organization(
    organization_id: uuid.UUID,
    body: OrganizationUpdateRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    superuser: Annotated[User, Depends(require_superuser)],
) -> OrganizationResponse:
    """Rename an organization (slug is immutable)."""
    org = await db.scalar(select(Organization).where(Organization.id == organization_id))
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization {organization_id} not found",
        )

    old_name = org.name
    org.name = body.name
    org.updated_at = datetime.now(UTC)

    await write_event(
        db,
        event_type="organization.updated",
        event_data={"old_name": old_name, "new_name": body.name},
        organization_id=org.id,
        user_id=superuser.id,
        session_id=_session_id(request),
        source_ip=_source_ip(request),
    )
    await db.commit()

    logger.info("organization_updated", organization_id=str(org.id))
    return _to_response(org)


@router.delete("/{organization_id}", response_model=OrganizationResponse)
async def delete_organization(
    organization_id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    superuser: Annotated[User, Depends(require_superuser)],
) -> OrganizationResponse:
    """Soft-delete an organization (is_active=False; data retained for audit)."""
    org = await db.scalar(select(Organization).where(Organization.id == organization_id))
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization {organization_id} not found",
        )
    if not org.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Organization {organization_id} is already deactivated",
        )

    org.is_active = False
    org.updated_at = datetime.now(UTC)

    await write_event(
        db,
        event_type="organization.deleted",
        event_data={"name": org.name, "slug": org.slug, "soft_delete": True},
        organization_id=org.id,
        user_id=superuser.id,
        session_id=_session_id(request),
        source_ip=_source_ip(request),
    )
    await db.commit()

    logger.info("organization_deleted", organization_id=str(org.id), slug=org.slug)
    return _to_response(org)
