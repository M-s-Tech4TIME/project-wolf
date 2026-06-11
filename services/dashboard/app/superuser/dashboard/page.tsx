"use client";

// Superuser landing — Phase 6.5-c-ii routing stub.
//
// Login routes the install-level Superuser here (ADR 0018: the
// org-selector is NEVER shown to the Superuser — they work at install
// scope, not org scope). The real install-admin surface (Organizations
// CRUD, per-org Admin creation, install-wide audit view) is Phase 6.5-d;
// this page exists so the redirect target is real and the Superuser
// session has a home that is not the org-scoped /chat.

import { ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function SuperuserDashboardPage() {
  const router = useRouter();
  const { isLoading, me, signOut } = useAuth();

  useEffect(() => {
    if (isLoading) return;
    if (!me) {
      router.replace("/login");
    } else if (me.role !== "superuser") {
      // Org users have no business here — back to their workspace.
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
    <main className="flex min-h-screen items-center justify-center bg-muted/30 px-4">
      <div className="w-full max-w-lg">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck className="h-5 w-5" />
              Superuser dashboard
            </CardTitle>
            <CardDescription>
              Signed in as <span className="font-medium">{me.display_name}</span> —
              the install-level administrator.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 text-sm text-muted-foreground">
            <p>
              The install-admin surface (Organizations management, per-org Admin
              creation, install-wide audit log) arrives in Phase 6.5-d. The
              backing APIs are already live.
            </p>
            <p>
              Per the organization-consent gate, this account has no data access
              inside any organization until that organization&apos;s Admin grants it.
            </p>
            <Button variant="outline" onClick={() => void signOut()}>
              Sign out
            </Button>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
