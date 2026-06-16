// Thin client for the wolf-server HTTP API.
//
// Phase 5.6-a (per ADR 0016): every request goes to wolf-dashboard's
// own origin under `/api/v1/...`, which is then reverse-proxied to
// wolf-server by `app/api/[...path]/route.ts`. The browser never
// sees a second origin. This eliminates the cross-origin NetworkError
// that surfaced under HTTPS in Phase 5.4.
//
// The session cookie set by wolf-server on login (HTTP-only,
// samesite=lax) round-trips through the proxy unchanged — the
// browser sees it as a cookie scoped to the dashboard's origin,
// which is now the only origin involved.

import { activeOrgHeader } from "./org-context";
import type {
  AccessApprove,
  AccessRequestCreate,
  ChatRequestBody,
  ChatResponseBody,
  InstallAuditPage,
  LoginRequest,
  LoginResponse,
  LoopEvent,
  Member,
  MemberCreate,
  MemberCreateResponse,
  MemberPasswordReset,
  MembershipInfo,
  MeResponse,
  Organization,
  OrganizationCreate,
  OrganizationMembership,
  OrganizationUpdate,
  OrgAccessRequest,
  OrgAuditPage,
  RecoveryAdminRequest,
  RecoveryAdminResponse,
  RegenerateInviteResponse,
  SuperuserAccessGrant,
  SuperuserAccessRequest,
  WazuhCredentialHistoryEntry,
  WazuhCredentialsResponse,
  WazuhCredentialsSaveResponse,
  WazuhCredentialsUpdate,
  WazuhTopologyResponse,
  WazuhTopologySaveResponse,
  WazuhTopologyUpdate,
} from "./types";

// All API calls are same-origin under `/api/v1/...`. No `apiBase()`
// helper is needed for the browser side — relative paths bind to
// whichever origin the page was served from.
const API_PREFIX = "";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function apiFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  return fetch(`${API_PREFIX}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      // Phase 6.5-c-ii: the per-tab active org rides on every call.
      // The cookie authenticates the user; this header names the org;
      // wolf-server validates the membership on each request.
      ...activeOrgHeader(),
      ...(init.headers ?? {}),
    },
  });
}

/**
 * Turn a FastAPI error `detail` into a human-readable string.
 *
 * FastAPI sends two shapes: a plain string (our `HTTPException(detail=...)`)
 * or — for request-validation (422) failures — a LIST of
 * `{loc, msg, type}` objects from pydantic. The naive `String(detail)` on
 * the list produced "[object Object]" in the UI; this formats each entry as
 * "field: message" (dropping the leading "body"/"query" location segment).
 */
function formatApiDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          const rec = item as { loc?: unknown; msg?: unknown };
          const field = Array.isArray(rec.loc)
            ? rec.loc.filter((p) => p !== "body" && p !== "query").join(".")
            : "";
          const msg = String(rec.msg);
          return field ? `${field}: ${msg}` : msg;
        }
        return typeof item === "string" ? item : JSON.stringify(item);
      })
      .filter(Boolean)
      .join("; ");
  }
  if (detail && typeof detail === "object") {
    try {
      return JSON.stringify(detail);
    } catch {
      return "Request failed";
    }
  }
  return String(detail);
}

async function unwrap<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let body: unknown;
    let detail = resp.statusText;
    // Clone so a non-JSON body can still be read as text in the fallback
    // (the original stream is consumed by .json()).
    const clone = resp.clone();
    try {
      body = await resp.json();
      if (body && typeof body === "object" && "detail" in body) {
        detail = formatApiDetail((body as { detail: unknown }).detail);
      }
    } catch {
      try {
        const text = await clone.text();
        if (text) detail = text;
      } catch {
        /* keep statusText */
      }
    }
    throw new ApiError(resp.status, detail, body);
  }
  return resp.json() as Promise<T>;
}

/** Like `unwrap` but for 204 No Content endpoints — surfaces a guided
 *  error on failure (via `formatApiDetail`) and returns nothing on success
 *  without trying to parse an empty body. */
async function unwrapNoContent(resp: Response): Promise<void> {
  if (resp.ok) return;
  let detail = resp.statusText;
  const clone = resp.clone();
  try {
    const body = await resp.json();
    if (body && typeof body === "object" && "detail" in body) {
      detail = formatApiDetail((body as { detail: unknown }).detail);
    }
  } catch {
    try {
      const text = await clone.text();
      if (text) detail = text;
    } catch {
      /* keep statusText */
    }
  }
  throw new ApiError(resp.status, detail);
}

// ── Auth ─────────────────────────────────────────────────────────────────

export function login(body: LoginRequest): Promise<LoginResponse> {
  return apiFetch("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify(body),
  }).then(unwrap<LoginResponse>);
}

export async function logout(): Promise<void> {
  await apiFetch("/api/v1/auth/logout", { method: "POST" });
}

export async function fetchMe(): Promise<MeResponse | null> {
  const resp = await apiFetch("/api/v1/auth/me");
  if (resp.status === 401) return null;
  return unwrap<MeResponse>(resp);
}

export function fetchMyOrganizations(): Promise<OrganizationMembership[]> {
  return apiFetch("/api/v1/auth/me/organizations").then(
    unwrap<OrganizationMembership[]>,
  );
}

/** Record the post-login org selection in the audit trail (optional per
 *  ADR 0018 — org routing itself is header-driven, not this call). */
export function selectOrganization(
  organizationId: string,
): Promise<MembershipInfo> {
  return apiFetch("/api/v1/auth/select-organization", {
    method: "POST",
    body: JSON.stringify({ organization_id: organizationId }),
  }).then(unwrap<MembershipInfo>);
}

/** Record a mid-session per-tab org switch in the audit trail. */
export function switchOrganization(
  organizationId: string,
): Promise<MembershipInfo> {
  return apiFetch("/api/v1/auth/switch-organization", {
    method: "POST",
    body: JSON.stringify({ organization_id: organizationId }),
  }).then(unwrap<MembershipInfo>);
}

// ── Chat ─────────────────────────────────────────────────────────────────

export function chat(body: ChatRequestBody): Promise<ChatResponseBody> {
  return apiFetch("/api/v1/chat", {
    method: "POST",
    body: JSON.stringify(body),
  }).then(unwrap<ChatResponseBody>);
}

/**
 * Stream chat events via SSE.  Calls `onEvent` for every loop event.
 * Returns when the stream completes (wolf-server sends `event: done`).
 *
 * Throws ApiError on HTTP-level failures (401 before the stream starts,
 * 500 from the backend, etc.).  Network or parse errors during streaming
 * bubble as plain Error.
 */
export async function chatStream(
  body: ChatRequestBody,
  onEvent: (event: LoopEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`${API_PREFIX}/api/v1/chat/stream`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...activeOrgHeader(),
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok) {
    throw new ApiError(resp.status, `chat stream failed: ${resp.statusText}`);
  }
  if (!resp.body) {
    throw new Error("chat stream: response had no body");
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line (\n\n).
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const event = parseFrame(frame);
      if (event && event.type !== "done") {
        onEvent(event as LoopEvent);
      }
    }
  }
}

function parseFrame(frame: string): { type: string; data: unknown } | null {
  let eventName: string | null = null;
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }
  if (!eventName) return null;
  const data = dataLines.length > 0 ? JSON.parse(dataLines.join("\n")) : {};
  return { type: eventName, data };
}

// ── Superuser install-admin (Phase 6.5-d) ──────────────────────────────────
// These hit Superuser-only routes gated by require_superuser; the Superuser
// has no active org, so apiFetch sends no X-Organization-Id header (correct —
// these endpoints are install-scoped, not org-scoped).

export function listOrganizations(): Promise<Organization[]> {
  return apiFetch("/api/v1/organizations").then(unwrap<Organization[]>);
}

export function createOrganization(
  body: OrganizationCreate,
): Promise<Organization> {
  return apiFetch("/api/v1/organizations", {
    method: "POST",
    body: JSON.stringify(body),
  }).then(unwrap<Organization>);
}

export function updateOrganization(
  organizationId: string,
  body: OrganizationUpdate,
): Promise<Organization> {
  return apiFetch(`/api/v1/organizations/${organizationId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  }).then(unwrap<Organization>);
}

/** Soft-delete (is_active=false) — the org's audit trail + rows survive. */
export function deleteOrganization(
  organizationId: string,
): Promise<Organization> {
  return apiFetch(`/api/v1/organizations/${organizationId}`, {
    method: "DELETE",
  }).then(unwrap<Organization>);
}

/** Break-glass: seed the first Admin into an org with zero Admins.
 *  Throws ApiError(409) when the org already has an active Admin. */
export function recoverOrganizationAdmin(
  organizationId: string,
  body: RecoveryAdminRequest,
): Promise<RecoveryAdminResponse> {
  return apiFetch(`/api/v1/organizations/${organizationId}/recovery/admin`, {
    method: "POST",
    body: JSON.stringify(body),
  }).then(unwrap<RecoveryAdminResponse>);
}

// ── Install-level Wazuh ecosystem topology (Phase 6.6-a/b) ──────────────────
// Superuser-only (require_superuser); org-less, so no X-Organization-Id header.

export function fetchWazuhTopology(): Promise<WazuhTopologyResponse> {
  return apiFetch("/api/v1/superuser/wazuh-topology").then(
    unwrap<WazuhTopologyResponse>,
  );
}

/** Configure / re-configure the install's Wazuh ecosystem topology. The
 *  backend probes every endpoint (validate-before-persist, HARD fail) — a
 *  400 ApiError means a required endpoint failed and nothing was saved. On
 *  success the response carries per-endpoint probe results + worker warnings. */
export function saveWazuhTopology(
  body: WazuhTopologyUpdate,
): Promise<WazuhTopologySaveResponse> {
  return apiFetch("/api/v1/superuser/wazuh-topology", {
    method: "PUT",
    body: JSON.stringify(body),
  }).then(unwrap<WazuhTopologySaveResponse>);
}

// ── Per-org Wazuh credentials (Phase 6.6-c/d) ───────────────────────────────
// Superuser-only; the org id is in the path (the Superuser has no membership
// in the org, so this is install-scoped config, not the active-org header).

export function fetchOrgWazuhCredentials(
  organizationId: string,
): Promise<WazuhCredentialsResponse> {
  return apiFetch(
    `/api/v1/superuser/organizations/${organizationId}/wazuh-credentials`,
  ).then(unwrap<WazuhCredentialsResponse>);
}

/** Save / rotate an org's Wazuh credentials. SOFT fail: the save succeeds even
 *  when the probe fails (so the Superuser can save before the Wazuh-side user
 *  is provisioned) — `probe_ok`/`warnings`/scope ride in the response. Throws
 *  ApiError(409) if no install ecosystem topology is configured yet. */
export function saveOrgWazuhCredentials(
  organizationId: string,
  body: WazuhCredentialsUpdate,
): Promise<WazuhCredentialsSaveResponse> {
  return apiFetch(
    `/api/v1/superuser/organizations/${organizationId}/wazuh-credentials`,
    { method: "PUT", body: JSON.stringify(body) },
  ).then(unwrap<WazuhCredentialsSaveResponse>);
}

/** This org's Wazuh credential-change audit trail (rotation log), newest first. */
export function fetchOrgWazuhCredentialHistory(
  organizationId: string,
): Promise<WazuhCredentialHistoryEntry[]> {
  return apiFetch(
    `/api/v1/superuser/organizations/${organizationId}/wazuh-credentials/history`,
  ).then(unwrap<WazuhCredentialHistoryEntry[]>);
}

export function fetchInstallAudit(
  limit = 50,
  offset = 0,
): Promise<InstallAuditPage> {
  const qs = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  return apiFetch(`/api/v1/superuser/audit?${qs}`).then(unwrap<InstallAuditPage>);
}

// ── Per-org user management (Phase 6.5-e) ──────────────────────────────────
// Org-scoped routes gated by USERS_MANAGE (Admin). The active-org header is
// sent automatically by apiFetch — these act on the caller's current org.

export function listMembers(): Promise<Member[]> {
  return apiFetch("/api/v1/organization/users").then(unwrap<Member[]>);
}

/** Add a member. If the email is a brand-new account, the response carries a
 *  one-time `new_password`; otherwise an existing user is added at `role`. */
export function createMember(body: MemberCreate): Promise<MemberCreateResponse> {
  return apiFetch("/api/v1/organization/users", {
    method: "POST",
    body: JSON.stringify(body),
  }).then(unwrap<MemberCreateResponse>);
}

/** Change a member's role. Throws ApiError(409) if it would leave the org
 *  without an active Admin, or for the Superuser's fixed role. */
export function changeMemberRole(
  userId: string,
  role: string,
): Promise<Member> {
  return apiFetch(`/api/v1/organization/users/${userId}/role`, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  }).then(unwrap<Member>);
}

/** Remove a member from the org. Throws ApiError(409) on the last-Admin guard. */
export async function removeMember(userId: string): Promise<void> {
  const resp = await apiFetch(`/api/v1/organization/users/${userId}`, {
    method: "DELETE",
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      if (body && typeof body === "object" && "detail" in body) {
        detail = String((body as { detail: unknown }).detail);
      }
    } catch {
      /* keep statusText */
    }
    throw new ApiError(resp.status, detail);
  }
}

/** Admin rotates a member's password (recovery — no SMTP). Returns the
 *  one-time password to share out of band. Throws ApiError(409) for the
 *  Superuser's fixed credential, 404 if not a member of this org. */
export function resetMemberPassword(userId: string): Promise<MemberPasswordReset> {
  return apiFetch(`/api/v1/organization/users/${userId}/password-reset`, {
    method: "POST",
  }).then(unwrap<MemberPasswordReset>);
}

/** Admin reissues a member's invite link (Phase 6.5-h) — the only way to
 *  recover a lost link, since only the token hash is stored. The old link
 *  stops working. Returns the raw token once. Throws ApiError(409) if the
 *  member is already verified, 404 if not a member of this org. */
export function regenerateInvite(userId: string): Promise<RegenerateInviteResponse> {
  return apiFetch(`/api/v1/organization/users/${userId}/regenerate-invite-link`, {
    method: "POST",
  }).then(unwrap<RegenerateInviteResponse>);
}

/** Consume an invite token to verify the current account (Phase 6.5-h).
 *  Authenticated: the user logs in first, then pastes the link their Admin
 *  delivered. Returns the refreshed identity. Throws ApiError(403) for an
 *  invalid/expired token, 409 if already verified. */
export function verifyInvite(token: string): Promise<MeResponse> {
  return apiFetch("/api/v1/auth/verify-invite", {
    method: "POST",
    body: JSON.stringify({ token }),
  }).then(unwrap<MeResponse>);
}

/** Break-glass: Superuser resets a user's password by EMAIL (6.5-e.2) — the
 *  recovery path for a locked-out sole Admin (no peer Admin, and the Superuser
 *  may not browse the roster to pick by id). Throws ApiError(404) if no such
 *  user, 409 for the Superuser's own credential. */
export function resetUserPasswordByEmail(
  email: string,
): Promise<MemberPasswordReset> {
  return apiFetch("/api/v1/users/password-reset-by-email", {
    method: "POST",
    body: JSON.stringify({ email }),
  }).then(unwrap<MemberPasswordReset>);
}

export function fetchOrgAudit(limit = 50, offset = 0): Promise<OrgAuditPage> {
  const qs = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  return apiFetch(`/api/v1/organization/audit?${qs}`).then(unwrap<OrgAuditPage>);
}

// ── Superuser-membership consent gate (Phase 6.5-f) ─────────────────────────

// Superuser side (require_superuser; org-less — no active-org header sent).

/** File a request for time-limited membership in an org. Throws
 *  ApiError(409) if the Superuser already has active access or an open
 *  pending request for this org, 404 if the org is absent/inactive. */
export function requestSuperuserAccess(
  organizationId: string,
  body: AccessRequestCreate,
): Promise<SuperuserAccessRequest> {
  return apiFetch(
    `/api/v1/superuser/organizations/${organizationId}/access-requests`,
    { method: "POST", body: JSON.stringify(body) },
  ).then(unwrap<SuperuserAccessRequest>);
}

/** The Superuser's own access-requests across every org, newest first. */
export function listMyAccessRequests(): Promise<SuperuserAccessRequest[]> {
  return apiFetch("/api/v1/superuser/access-requests").then(
    unwrap<SuperuserAccessRequest[]>,
  );
}

/** Cancel one of the Superuser's own pending requests. Throws
 *  ApiError(409) if it is no longer pending, 404 if not found. */
export function cancelAccessRequest(requestId: string): Promise<void> {
  return apiFetch(`/api/v1/superuser/access-requests/${requestId}`, {
    method: "DELETE",
  }).then(unwrapNoContent);
}

// Admin side (SUPERUSER_MEMBERSHIP_GRANT; acts on the caller's active org).

/** This org's Superuser access-requests — pending first, then newest. */
export function listOrgAccessRequests(): Promise<OrgAccessRequest[]> {
  return apiFetch("/api/v1/organization/access-requests").then(
    unwrap<OrgAccessRequest[]>,
  );
}

/** Approve a pending request → create the time-limited grant. The Admin may
 *  honour the requested duration, override it, or grant "until revoked".
 *  Throws ApiError(409) if already decided or the Superuser is already active. */
export function approveAccessRequest(
  requestId: string,
  body: AccessApprove,
): Promise<OrgAccessRequest> {
  return apiFetch(`/api/v1/organization/access-requests/${requestId}/approve`, {
    method: "POST",
    body: JSON.stringify(body),
  }).then(unwrap<OrgAccessRequest>);
}

/** Reject a pending request — no grant is created. */
export function rejectAccessRequest(
  requestId: string,
  reason?: string,
): Promise<OrgAccessRequest> {
  return apiFetch(`/api/v1/organization/access-requests/${requestId}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason: reason ?? null }),
  }).then(unwrap<OrgAccessRequest>);
}

/** Revoke the Superuser's active grant in this org immediately. Throws
 *  ApiError(404) if there is no active grant. */
export function revokeSuperuserMembership(): Promise<void> {
  return apiFetch("/api/v1/organization/memberships/superuser", {
    method: "DELETE",
  }).then(unwrapNoContent);
}

// Any active member — the transparency banner.

/** The org's current active Superuser grant, or null. Readable by every
 *  member of the org; the backend runs lazy expiry so a lapsed grant
 *  returns null. */
export function fetchSuperuserAccess(): Promise<SuperuserAccessGrant | null> {
  return apiFetch("/api/v1/organization/superuser-access").then(
    unwrap<SuperuserAccessGrant | null>,
  );
}
