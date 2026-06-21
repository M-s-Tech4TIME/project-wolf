// Client-side role→capability checks for the action-approval surfaces
// (Phase 6, ADR 0025).
//
// The backend is the sole authority: every action-proposal endpoint is gated
// by `require_capability(...)` and 403s a role that lacks it. These helpers are
// UX-only — they decide whether to *show* the queue nav / Approve buttons, so a
// user isn't offered a control the server would reject. They mirror
// `ROLE_CAPABILITIES` in services/server/wolf_server/organization/rbac.py;
// keep the two in lockstep (a role gains/loses an action capability there).
//
// Note: "superuser" is intentionally excluded — a Superuser acts on orgs only
// via a time-limited grant (role="superuser"), which carries no ACTION_* caps;
// org action management belongs to org roles (superuser-config-authority).

/** Roles allowed to PROPOSE actions — i.e. to view the approval queue
 *  (Capability.ACTION_PROPOSE: analyst, responder, engineer, admin). */
const PROPOSE_ROLES = new Set(["analyst", "responder", "engineer", "admin"]);

/** Roles allowed to APPROVE/REJECT proposals
 *  (Capability.ACTION_APPROVE: responder, engineer, admin — NOT analyst). */
const APPROVE_ROLES = new Set(["responder", "engineer", "admin"]);

/** Can this role see the action-approval queue? */
export function canProposeActions(role: string | undefined | null): boolean {
  return !!role && PROPOSE_ROLES.has(role);
}

/** Can this role approve/reject a proposal (separation of duties is enforced
 *  server-side: the requester can't approve their own, regardless of role)? */
export function canApproveActions(role: string | undefined | null): boolean {
  return !!role && APPROVE_ROLES.has(role);
}
