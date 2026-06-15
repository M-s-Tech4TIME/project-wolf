"use client";

// Org-scoped settings shell — Phase 6.5-e.
//
// Guard mirrors app/chat/layout.tsx (no session → /login) plus an Admin role
// gate: only the current org's Admin may reach /settings/* (the Users page
// today; User Settings / Wolf Configuration later). me.role reflects the
// per-tab active org (the X-Organization-Id header), so a non-Admin — or a
// Superuser, or an org-less tab — is bounced back to /chat.

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useAuth } from "@/components/auth-provider";
import { cn } from "@/lib/utils";

const SETTINGS_TABS = [
  { href: "/settings/users", label: "Users" },
  { href: "/settings/access", label: "Access" },
] as const;

export default function SettingsLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { isLoading, me } = useAuth();

  useEffect(() => {
    if (isLoading) return;
    if (!me) {
      router.replace("/login");
    } else if (me.role !== "admin") {
      router.replace("/chat");
    }
  }, [isLoading, me, router]);

  if (isLoading || !me || me.role !== "admin") {
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
          <span className="text-sm font-medium">Organization settings</span>
        </div>
        <nav className="mx-auto flex w-full max-w-5xl items-center gap-1 px-4">
          {SETTINGS_TABS.map((tab) => {
            const active = pathname === tab.href || pathname.startsWith(`${tab.href}/`);
            return (
              <Link
                key={tab.href}
                href={tab.href}
                className={cn(
                  "border-b-2 px-3 py-2 text-sm transition-colors",
                  active
                    ? "border-foreground font-medium text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground",
                )}
              >
                {tab.label}
              </Link>
            );
          })}
        </nav>
      </header>
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6">{children}</main>
    </div>
  );
}
