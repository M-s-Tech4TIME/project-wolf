"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import { fetchMe, fetchMyOrganizations, logout as apiLogout, ApiError } from "@/lib/api";
import {
  getActiveOrganizationId,
  setActiveOrganizationId,
} from "@/lib/org-context";
import { clearDismissedGrantKey } from "@/lib/su-banner-dismiss";
import type { MeResponse, OrganizationMembership } from "@/lib/types";

type AuthState = {
  isLoading: boolean;
  me: MeResponse | null;
  organizations: OrganizationMembership[];
  /** The per-tab active org (Phase 6.5-c-ii). Null for org-less sessions
   *  (Superuser) or a multi-org user who hasn't picked yet in this tab. */
  activeOrganizationId: string | null;
  /** Set the tab's active org: writes per-tab sessionStorage (so every
   *  API call carries X-Organization-Id) and re-renders consumers. */
  setActiveOrganization: (organizationId: string | null) => void;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isLoading, setIsLoading] = useState(true);
  const [me, setMe] = useState<MeResponse | null>(null);
  const [organizations, setOrganizations] = useState<OrganizationMembership[]>([]);
  // Hydrated from sessionStorage in the bootstrap effect below (reading
  // it during render would mismatch the SSR-rendered HTML).
  const [activeOrganizationId, setActiveOrgState] = useState<string | null>(null);
  const router = useRouter();

  const setActiveOrganization = useCallback((organizationId: string | null) => {
    setActiveOrganizationId(organizationId); // sessionStorage (api layer reads this)
    setActiveOrgState(organizationId); // React state (UI reads this)
  }, []);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      let fetched: MeResponse | null;
      try {
        fetched = await fetchMe();
      } catch (err) {
        // 403 = the stored org is no longer ours (membership revoked, org
        // deactivated). Self-heal: drop the stale tab context, retry org-less.
        if (err instanceof ApiError && err.status === 403) {
          setActiveOrganization(null);
          fetched = await fetchMe();
        } else {
          throw err;
        }
      }
      setMe(fetched);
      if (fetched) {
        const memberships = await fetchMyOrganizations();
        setOrganizations(memberships);
        const active = getActiveOrganizationId();
        if (active && !memberships.some((m) => m.id === active)) {
          // Stale tab context (e.g. membership removed while away).
          setActiveOrganization(null);
        } else if (!active && memberships.length === 1 && fetched.role !== "superuser") {
          // Fresh tab, exactly one org: auto-select it so single-org users
          // never see a picker (mirrors login's auto-select shape).
          setActiveOrganization(memberships[0].id);
        }
      } else {
        setOrganizations([]);
      }
    } finally {
      setIsLoading(false);
    }
  }, [setActiveOrganization]);

  const signOut = useCallback(async () => {
    await apiLogout();
    // The org context belongs to the session that just ended.
    setActiveOrganization(null);
    // A dismissed Superuser-access banner shouldn't carry into the next
    // login — clear it so an active grant re-surfaces (6.5-f).
    clearDismissedGrantKey();
    setMe(null);
    setOrganizations([]);
    router.push("/login");
  }, [router, setActiveOrganization]);

  useEffect(() => {
    // Bootstrap auth on mount.  setState inside an effect is legitimate for
    // "subscribe to external state" cases — this is one of them.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setActiveOrgState(getActiveOrganizationId());
    void refresh();
  }, [refresh]);

  return (
    <AuthContext.Provider
      value={{
        isLoading,
        me,
        organizations,
        activeOrganizationId,
        setActiveOrganization,
        refresh,
        signOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be called inside <AuthProvider>");
  }
  return ctx;
}
