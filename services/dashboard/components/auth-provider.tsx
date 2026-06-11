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

import { fetchMe, fetchMyOrganizations, logout as apiLogout } from "@/lib/api";
import type { MeResponse, OrganizationMembership } from "@/lib/types";

type AuthState = {
  isLoading: boolean;
  me: MeResponse | null;
  organizations: OrganizationMembership[];
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isLoading, setIsLoading] = useState(true);
  const [me, setMe] = useState<MeResponse | null>(null);
  const [organizations, setOrganizations] = useState<OrganizationMembership[]>([]);
  const router = useRouter();

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const fetched = await fetchMe();
      setMe(fetched);
      if (fetched) {
        const memberships = await fetchMyOrganizations();
        setOrganizations(memberships);
      } else {
        setOrganizations([]);
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  const signOut = useCallback(async () => {
    await apiLogout();
    setMe(null);
    setOrganizations([]);
    router.push("/login");
  }, [router]);

  useEffect(() => {
    // Bootstrap auth on mount.  setState inside an effect is legitimate for
    // "subscribe to external state" cases — this is one of them.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  return (
    <AuthContext.Provider value={{ isLoading, me, organizations, refresh, signOut }}>
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
