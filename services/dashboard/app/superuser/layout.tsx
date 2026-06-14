"use client";

// Superuser install-admin shell — Phase 6.5-d.
//
// One guard + nav for every /superuser/* page (Dashboard, Organizations,
// Audit). The guard is lifted here so individual pages don't repeat it:
// no session → /login; an org user (role !== "superuser") has no business
// in the install-admin surface → /chat. ADR 0018: the Superuser is an
// install-level identity with no org data access.

import { Building2, ScrollText, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/superuser/dashboard", label: "Dashboard", icon: ShieldCheck },
  { href: "/superuser/organizations", label: "Organizations", icon: Building2 },
  { href: "/superuser/audit", label: "Audit log", icon: ScrollText },
];

export default function SuperuserLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { isLoading, me, signOut } = useAuth();

  useEffect(() => {
    if (isLoading) return;
    if (!me) {
      router.replace("/login");
    } else if (me.role !== "superuser") {
      router.replace("/chat");
    }
  }, [isLoading, me, router]);

  if (isLoading || !me || me.role !== "superuser") {
    return (
      <div className="flex h-screen items-center justify-center text-muted-foreground">
        Loading…
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-foreground/10 bg-card">
        <div className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-4 py-3">
          <div className="flex items-center gap-2 font-semibold">
            <ShieldCheck className="h-5 w-5" />
            <span>Wolf — install admin</span>
          </div>
          <div className="flex items-center gap-3 text-sm text-muted-foreground">
            <span className="hidden sm:inline">{me.display_name}</span>
            <Button variant="outline" size="sm" onClick={() => void signOut()}>
              Sign out
            </Button>
          </div>
        </div>
        <nav className="mx-auto flex w-full max-w-6xl gap-1 px-2">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || pathname.startsWith(`${href}/`);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-2 border-b-2 px-3 py-2 text-sm transition-colors",
                  active
                    ? "border-foreground font-medium text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4" />
                {label}
              </Link>
            );
          })}
        </nav>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">{children}</main>
    </div>
  );
}
