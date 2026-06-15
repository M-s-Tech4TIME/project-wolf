"use client";

// All-member transparency banner — Phase 6.5-f (ADR 0018 consent gate).
//
// When the install Superuser holds an active, time-limited membership in
// the active org, EVERY member of that org sees this strip. It's derived
// from live grant state (no notifications table): the backend's
// GET /api/v1/organization/superuser-access returns the current grant or
// null and runs lazy expiry, so the banner self-clears the moment a grant
// lapses or is revoked. Refreshed on mount, route change, window focus,
// and a light ~90s poll (there is no SSE/websocket channel in the stack).
//
// Dismiss FULLY hides it (operator choice, 2026-06-15) — no trace remains.
// The dismissal is per-grant (sessionStorage, keyed by org + grant start),
// so a brand-new grant re-shows the banner, and signing out clears it so
// the next login surfaces an active grant again. See lib/su-banner-dismiss.

import { ShieldAlert, X } from "lucide-react";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { useAuth } from "@/components/auth-provider";
import { fetchSuperuserAccess } from "@/lib/api";
import { absoluteTimeTitle, timeUntil } from "@/lib/format";
import { getDismissedGrantKey, setDismissedGrantKey } from "@/lib/su-banner-dismiss";
import type { SuperuserAccessGrant } from "@/lib/types";

const POLL_MS = 90_000;

export function SuperuserAccessBanner() {
  const { activeOrganizationId } = useAuth();
  const pathname = usePathname();
  const [grant, setGrant] = useState<SuperuserAccessGrant | null>(null);
  const [dismissedKey, setDismissedKey] = useState<string | null>(null);

  const refresh = useCallback(() => {
    // No active org → nothing to fetch; the render guard hides the banner.
    // (We never setState synchronously here — only via the fetch promise.)
    if (!activeOrganizationId) return;
    fetchSuperuserAccess()
      .then(setGrant)
      .catch(() => {
        /* transient (offline / 401 mid-navigation) — keep last known state */
      });
  }, [activeOrganizationId]);

  // Hydrate the dismissal from sessionStorage on mount (reading it during
  // render would mismatch the SSR-rendered HTML).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDismissedKey(getDismissedGrantKey());
  }, []);

  // Fetch on mount and whenever the active org or route changes.
  useEffect(() => {
    refresh();
  }, [refresh, pathname]);

  // Light background poll + refetch on window focus.
  useEffect(() => {
    if (!activeOrganizationId) return;
    const id = window.setInterval(refresh, POLL_MS);
    const onFocus = () => refresh();
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, [refresh, activeOrganizationId]);

  if (!activeOrganizationId || !grant) return null;
  // Identity of THIS grant; a new grant (different start) gets a new key
  // and so re-shows even if a prior grant was dismissed.
  const grantKey = `${activeOrganizationId}:${grant.granted_at}`;
  if (dismissedKey === grantKey) return null;

  function dismiss() {
    setDismissedGrantKey(grantKey);
    setDismissedKey(grantKey);
  }

  return (
    <div className="border-b border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-200">
      <div className="mx-auto flex w-full items-center gap-2 px-4 py-1.5 text-sm">
        <ShieldAlert className="h-4 w-4 shrink-0" />
        <span className="flex-1">
          <span className="font-medium">
            Wolf Superuser has temporary read &amp; chat access to this
            organization.
          </span>{" "}
          {grant.granted_by_display_name
            ? `Granted by ${grant.granted_by_display_name}. `
            : ""}
          {grant.expires_at ? (
            <>
              Expires{" "}
              <span title={absoluteTimeTitle(grant.expires_at)}>
                {timeUntil(grant.expires_at)}
              </span>
              .
            </>
          ) : (
            "Active until revoked."
          )}
        </span>
        <button
          type="button"
          onClick={dismiss}
          className="shrink-0 rounded p-0.5 hover:bg-amber-500/20"
          aria-label="Dismiss access notice"
          title="Dismiss"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
