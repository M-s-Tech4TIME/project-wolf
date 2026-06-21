"use client";

// Action-approval shell — Phase 6 6-b (ADR 0025).
//
// Guard mirrors app/settings/layout.tsx but gates on ACTION_PROPOSE instead of
// the Admin role: any role that can produce or review proposals (analyst,
// responder, engineer, admin) may reach /actions. me.role reflects the per-tab
// active org, so a viewer — or a Superuser, or an org-less tab — is bounced
// back to /chat. The backend gates every call independently; this is UX.

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useAuth } from "@/components/auth-provider";
import { canProposeActions } from "@/lib/capabilities";

export default function ActionsLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { isLoading, me } = useAuth();
  const allowed = canProposeActions(me?.role);

  useEffect(() => {
    if (isLoading) return;
    if (!me) {
      router.replace("/login");
    } else if (!allowed) {
      router.replace("/chat");
    }
  }, [isLoading, me, allowed, router]);

  if (isLoading || !me || !allowed) {
    return (
      <div className="flex h-screen items-center justify-center text-muted-foreground">
        Loading…
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-foreground/10 bg-card">
        <div className="mx-auto flex w-full max-w-5xl items-center justify-between gap-4 px-4 py-3">
          <Link
            href="/chat"
            className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to chat
          </Link>
          <span className="text-sm font-medium">Action approvals</span>
        </div>
      </header>
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6">{children}</main>
    </div>
  );
}
