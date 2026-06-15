// Per-tab dismissal state for the Superuser-access transparency banner
// (Phase 6.5-f). Operator choice (2026-06-15): dismiss FULLY hides the
// banner with no trace. The dismissal is keyed by the specific grant
// (org id + grant start time) and kept in sessionStorage — per-tab, like
// the active-org context. Consequences:
//   - within one grant + tab session, once dismissed it stays hidden;
//   - a brand-new grant has a different key, so the banner re-shows;
//   - sign-out clears it (see auth-provider), so the next login surfaces
//     an active grant again.

const KEY = "wolf:su-banner-dismissed";

export function getDismissedGrantKey(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(KEY);
}

export function setDismissedGrantKey(grantKey: string): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(KEY, grantKey);
}

export function clearDismissedGrantKey(): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.removeItem(KEY);
}
