// Per-tab active-organization state — Phase 6.5-c-ii (ADR 0018 Round 3).
//
// The session cookie authenticates the USER; the active ORGANIZATION is
// per-tab state that every API call carries in the `X-Organization-Id`
// header. `sessionStorage` is the per-tab persistence layer (it survives
// reloads but is NOT shared across tabs — that is the point: two tabs can
// work in two different organizations concurrently).
//
// This module is deliberately framework-free so `lib/api.ts` (plain
// functions, no React) can read the active org synchronously when
// building request headers. React components get the same state
// reactively via the AuthProvider, which writes through this module.

const STORAGE_KEY = "wolf.active_organization_id";

export const ORG_HEADER = "X-Organization-Id";

export function getActiveOrganizationId(): string | null {
  // SSR / prerender: no window, no org context.
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(STORAGE_KEY);
  } catch {
    // sessionStorage can throw in privacy modes; treat as no context.
    return null;
  }
}

export function setActiveOrganizationId(organizationId: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (organizationId === null) {
      window.sessionStorage.removeItem(STORAGE_KEY);
    } else {
      window.sessionStorage.setItem(STORAGE_KEY, organizationId);
    }
  } catch {
    // Ignore storage failures — the in-memory React state still drives
    // the current tab; only reload-persistence is lost.
  }
}

/** Headers fragment for the active org; empty when no org is active
 *  (org-less Superuser session, or pre-selection multi-org session). */
export function activeOrgHeader(): Record<string, string> {
  const id = getActiveOrganizationId();
  return id ? { [ORG_HEADER]: id } : {};
}
