"use client";

// Superuser overview hub — Phase 6.5-d.
//
// The guard + nav + sign-out live in app/superuser/layout.tsx; this page
// is just the landing content: a quick org count and entry points into
// Organizations and the install-wide audit log.

import { Building2, ScrollText } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/auth-provider";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ApiError, listOrganizations } from "@/lib/api";

export default function SuperuserDashboardPage() {
  const { me } = useAuth();
  const [orgCount, setOrgCount] = useState<number | null>(null);
  const [activeCount, setActiveCount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listOrganizations()
      .then((orgs) => {
        if (cancelled) return;
        setOrgCount(orgs.length);
        setActiveCount(orgs.filter((o) => o.is_active).length);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.message : "Failed to load organizations");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Welcome, {me?.display_name}</h1>
        <p className="text-sm text-muted-foreground">
          You are the install-level administrator.
        </p>
      </div>

      {error ? (
        <Alert variant="destructive">
          <AlertTitle>Couldn&apos;t load organizations</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2">
        <Link href="/superuser/organizations" className="block">
          <Card className="h-full px-5 transition-colors hover:ring-foreground/25">
            <CardHeader className="px-0">
              <CardTitle className="flex items-center gap-2 text-base">
                <Building2 className="h-5 w-5" />
                Organizations
              </CardTitle>
              <CardDescription>
                {orgCount === null
                  ? "Loading…"
                  : `${activeCount} active · ${orgCount} total`}
              </CardDescription>
            </CardHeader>
            <CardContent className="px-0 text-sm text-muted-foreground">
              Create organizations, rename or soft-delete them, and seed each
              one&apos;s first Admin.
            </CardContent>
          </Card>
        </Link>

        <Link href="/superuser/audit" className="block">
          <Card className="h-full px-5 transition-colors hover:ring-foreground/25">
            <CardHeader className="px-0">
              <CardTitle className="flex items-center gap-2 text-base">
                <ScrollText className="h-5 w-5" />
                Audit log
              </CardTitle>
              <CardDescription>Install-wide</CardDescription>
            </CardHeader>
            <CardContent className="px-0 text-sm text-muted-foreground">
              Every organization&apos;s events plus system-level activity, newest
              first.
            </CardContent>
          </Card>
        </Link>
      </div>

      <Alert>
        <AlertTitle>Organization-consent gate</AlertTitle>
        <AlertDescription>
          Per ADR 0018, this account has no data access inside any organization
          until that organization&apos;s Admin grants it. You can create
          organizations and seed their first Admin, but you cannot read their
          data.
        </AlertDescription>
      </Alert>
    </div>
  );
}
