"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useAuth } from "@/components/auth-provider";

export default function ChatLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { isLoading, me, organizations } = useAuth();

  // ADR 0018 consent gate: the install Superuser has NO org data access
  // until an org Admin grants it. With zero active memberships there is no
  // organization to chat about, so bounce them to the install-admin
  // dashboard — its Chat nav unlocks the moment a grant lands. Regular org
  // users always hold ≥1 membership, so this never affects them.
  const superuserWithoutOrg = me?.role === "superuser" && organizations.length === 0;
  // Phase 6.5-h: an unverified org user can't reach chat (the backend gate
  // 403s every org call) — send them to paste their invite link instead.
  const unverified = !!me && me.role !== "superuser" && me.verification_status !== "verified";

  useEffect(() => {
    if (isLoading) return;
    if (!me) {
      router.replace("/login");
    } else if (superuserWithoutOrg) {
      router.replace("/superuser/dashboard");
    } else if (unverified) {
      router.replace("/verify");
    }
  }, [isLoading, me, superuserWithoutOrg, unverified, router]);

  if (isLoading || !me || superuserWithoutOrg || unverified) {
    return (
      <div className="flex h-screen items-center justify-center text-muted-foreground">
        Loading…
      </div>
    );
  }

  return <>{children}</>;
}
