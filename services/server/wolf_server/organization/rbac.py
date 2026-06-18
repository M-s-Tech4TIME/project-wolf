"""Role-based access control — Phase 6.5-b, ADR 0018.

The capability matrix from ADR 0018 §"Decision: per-organization RBAC".
The propose/approve rows land with Phase 6 (capability-driven action
execution, ADR 0025): ``ACTION_PROPOSE`` (produce a reviewable proposal —
low-risk, it's just data) and ``ACTION_APPROVE`` (sign a pending proposal so
the in-process gateway may execute it).  There is intentionally no
``ACTION_EXECUTE`` capability: execution is system-internal (the gateway,
after a human approval), never a role a user is granted — doc 03's "no tier
lets one actor both decide and perform a state change" survives the reframe.

Roles attach to the UserOrganization membership row, never to the User:
one person can be Admin in org A and Analyst in org B.  The "superuser"
role value marks the Superuser's own time-limited membership (granted
by an org Admin via the consent gate, 6.5-f) — within an org it behaves
like a read/chat member, NOT like an Admin.

Usage::

    @router.get("/api/v1/organization/audit")
    async def view_audit(
        ctx: Annotated[
            OrganizationContext, Depends(require_capability(Capability.AUDIT_LOG_VIEW))
        ],
    ) -> ...: ...
"""

import enum
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wolf_server.organization.context import (
    OrganizationContext,
    require_organization_context,
)
from wolf_server.organization.models import User, UserOrganization


class Capability(enum.StrEnum):
    """Org-scoped capabilities.

    Phase 6 (ADR 0025) added ``ACTION_PROPOSE`` + ``ACTION_APPROVE``.  There is
    deliberately no ``ACTION_EXECUTE`` — execution is system-internal (gateway,
    post-approval), never a user-granted role.
    """

    # Admin-only: the org-consent gate (ADR 0018) — granting/revoking the
    # Superuser's membership in this org.
    SUPERUSER_MEMBERSHIP_GRANT = "superuser_membership_grant"
    # Admin-only: create users in the org, assign/change roles, remove
    # memberships.
    USERS_MANAGE = "users_manage"
    # Admin + Engineer: org-level settings (RAG corpus, prompts, model
    # selection, embedding provider).
    ORG_SETTINGS_CONFIGURE = "org_settings_configure"
    # Admin + Engineer: wolf-pack agent deployment.  The gate exists now;
    # the deploy infrastructure ships with Phase 12.
    WOLF_PACK_DEPLOY = "wolf_pack_deploy"
    # All org members (membership IS the grant).
    CHAT = "chat"
    # Admin + Responder only.
    AUDIT_LOG_VIEW = "audit_log_view"
    # All org members: read alerts / agents / knowledge.
    DATA_READ = "data_read"
    # Phase 6 (ADR 0025): produce a reviewable action proposal (changes
    # nothing itself — a proposal is just data placed in the approval queue).
    ACTION_PROPOSE = "action_propose"
    # Phase 6 (ADR 0025): sign a pending proposal so the in-process gateway
    # may execute it.  Separation of duties (requester != approver) is enforced
    # structurally in gateway/approval.py, on top of this role gate.
    ACTION_APPROVE = "action_approve"


_MEMBER_BASELINE = frozenset({Capability.CHAT, Capability.DATA_READ})

# The enforcement source of truth.  Keep in lockstep with the capability
# matrix in ADR 0018 — any divergence is a bug in whichever changed last.
ROLE_CAPABILITIES: dict[str, frozenset[Capability]] = {
    "admin": _MEMBER_BASELINE
    | {
        Capability.SUPERUSER_MEMBERSHIP_GRANT,
        Capability.USERS_MANAGE,
        Capability.ORG_SETTINGS_CONFIGURE,
        Capability.WOLF_PACK_DEPLOY,
        Capability.AUDIT_LOG_VIEW,
        Capability.ACTION_PROPOSE,
        Capability.ACTION_APPROVE,
    },
    "engineer": _MEMBER_BASELINE
    | {
        Capability.ORG_SETTINGS_CONFIGURE,
        Capability.WOLF_PACK_DEPLOY,
        Capability.ACTION_PROPOSE,
        Capability.ACTION_APPROVE,
    },
    "responder": _MEMBER_BASELINE
    | {
        Capability.AUDIT_LOG_VIEW,
        Capability.ACTION_PROPOSE,
        Capability.ACTION_APPROVE,
    },
    # Analyst proposes but does not approve — a natural producer/approver
    # split on top of the structural separation-of-duties check.
    "analyst": _MEMBER_BASELINE | {Capability.ACTION_PROPOSE},
    # The Superuser's consented org membership: read + chat only.  Org
    # governance (users, settings, audit) stays with the org's own roles.
    "superuser": _MEMBER_BASELINE,
}


def role_has_capability(role: str, capability: Capability) -> bool:
    return capability in ROLE_CAPABILITIES.get(role, frozenset())


def require_capability(
    capability: Capability,
) -> Callable[[OrganizationContext], Awaitable[OrganizationContext]]:
    """Build a FastAPI dependency enforcing one capability-matrix row.

    Layers on require_organization_context, so by the time the role is
    checked the session is authenticated AND the membership binding is
    confirmed active.  403 carries the capability name so the operator
    can map a refusal straight back to the ADR matrix.
    """

    async def _dependency(
        ctx: Annotated[OrganizationContext, Depends(require_organization_context)],
    ) -> OrganizationContext:
        if not role_has_capability(ctx.role, capability):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role {ctx.role!r} does not have the "
                    f"{capability.value!r} capability in this organization"
                ),
            )
        return ctx

    return _dependency


async def count_other_active_admins(
    db: AsyncSession,
    organization_id: uuid.UUID,
    excluding_user_id: uuid.UUID,
) -> int:
    """Count active Admins of the org other than the given user."""
    result = await db.scalar(
        select(func.count())
        .select_from(UserOrganization)
        .join(User, User.id == UserOrganization.user_id)
        .where(
            UserOrganization.organization_id == organization_id,
            UserOrganization.role == "admin",
            UserOrganization.user_id != excluding_user_id,
            User.is_active.is_(True),
        )
    )
    return int(result or 0)


async def ensure_not_last_admin(
    db: AsyncSession,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """The "Last Admin" invariant guard (ADR 0018 §Role-change discipline).

    Raise 409 if removing/demoting `user_id`'s Admin role would leave the
    organization with zero active Admins.  Callers invoke this BEFORE
    demoting an Admin, removing an Admin's membership, or deactivating an
    Admin's account.  (Recovery for orgs that somehow reach zero Admins:
    the break-glass endpoint in api/superuser.py.)
    """
    others = await count_other_active_admins(db, organization_id, user_id)
    if others == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This is the organization's last active Admin — assign "
                "another Admin first. An organization must always have at "
                "least one active Admin."
            ),
        )
