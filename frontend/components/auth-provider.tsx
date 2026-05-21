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

import { fetchMe, fetchMyTenants, logout as apiLogout } from "@/lib/api";
import type { MeResponse, TenantMembership } from "@/lib/types";

type AuthState = {
  isLoading: boolean;
  me: MeResponse | null;
  tenants: TenantMembership[];
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isLoading, setIsLoading] = useState(true);
  const [me, setMe] = useState<MeResponse | null>(null);
  const [tenants, setTenants] = useState<TenantMembership[]>([]);
  const router = useRouter();

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const fetched = await fetchMe();
      setMe(fetched);
      if (fetched) {
        const memberships = await fetchMyTenants();
        setTenants(memberships);
      } else {
        setTenants([]);
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  const signOut = useCallback(async () => {
    await apiLogout();
    setMe(null);
    setTenants([]);
    router.push("/login");
  }, [router]);

  useEffect(() => {
    // Bootstrap auth on mount.  setState inside an effect is legitimate for
    // "subscribe to external state" cases — this is one of them.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  return (
    <AuthContext.Provider value={{ isLoading, me, tenants, refresh, signOut }}>
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
