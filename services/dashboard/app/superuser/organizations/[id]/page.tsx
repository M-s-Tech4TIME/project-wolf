"use client";

// Per-organization detail + initial-Admin seeding — Phase 6.5-d.
//
// The Superuser can seed an org's FIRST Admin via the break-glass
// recovery endpoint (works only while the org has zero active Admins).
// Per ADR 0018's consent gate this page shows NO org data (no member
// list) — once an Admin exists, user management is the org Admin's job
// (Phase 6.5-e), and the backend returns 409 here.

import { ArrowLeft, Check, Copy, KeyRound } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, listOrganizations, recoverOrganizationAdmin } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { absoluteTimeTitle } from "@/lib/format";
import type { Organization, RecoveryAdminResponse } from "@/lib/types";

export default function OrganizationDetailPage() {
  const params = useParams<{ id: string }>();
  const orgId = params.id;

  const [org, setOrg] = useState<Organization | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Seed-admin form
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [alreadyHasAdmin, setAlreadyHasAdmin] = useState(false);
  const [result, setResult] = useState<RecoveryAdminResponse | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    // No single-org GET endpoint exists; the list is install-scoped and
    // small, so fetch it and pick this org out.
    listOrganizations()
      .then((orgs) => {
        if (cancelled) return;
        const found = orgs.find((o) => o.id === orgId) ?? null;
        if (found === null) setNotFound(true);
        else setOrg(found);
      })
      .catch((e) => {
        if (cancelled) return;
        setLoadError(e instanceof ApiError ? e.message : "Failed to load organization");
      });
    return () => {
      cancelled = true;
    };
  }, [orgId]);

  async function submitSeed() {
    const trimmedEmail = email.trim();
    if (!trimmedEmail) return setFormError("Email is required.");
    setBusy(true);
    setFormError(null);
    setAlreadyHasAdmin(false);
    try {
      const res = await recoverOrganizationAdmin(orgId, {
        email: trimmedEmail,
        display_name: displayName.trim() || "Organization Admin",
      });
      setResult(res);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setAlreadyHasAdmin(true);
      } else {
        setFormError(e instanceof ApiError ? e.message : "Failed to create Admin.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function copyPassword() {
    if (!result?.new_password) return;
    if (await copyText(result.new_password)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }

  if (notFound) {
    return (
      <div className="space-y-4">
        <BackLink />
        <Alert variant="destructive">
          <AlertTitle>Organization not found</AlertTitle>
          <AlertDescription>
            No organization matches this id. It may have been removed.
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <BackLink />

      {loadError ? (
        <Alert variant="destructive">
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          {org?.name ?? "Loading…"}
          {org && !org.is_active ? <Badge variant="outline">Deleted</Badge> : null}
        </h1>
        {org ? (
          <p className="text-sm text-muted-foreground">
            <span className="font-mono">{org.slug}</span> · created{" "}
            {absoluteTimeTitle(org.created_at)}
          </p>
        ) : null}
      </div>

      <Card className="px-5">
        <CardHeader className="px-0">
          <CardTitle className="flex items-center gap-2 text-base">
            <KeyRound className="h-5 w-5" />
            Seed the first Admin
          </CardTitle>
          <CardDescription>
            Break-glass: create the organization&apos;s initial Admin. Available
            only while the org has zero Admins — afterwards, user management is
            the Admin&apos;s responsibility.
          </CardDescription>
        </CardHeader>
        <CardContent className="px-0">
          {result ? (
            <div className="space-y-3">
              <Alert>
                <AlertTitle>Admin created</AlertTitle>
                <AlertDescription>
                  <span className="font-medium">{result.email}</span> is now an
                  Admin of this organization.
                </AlertDescription>
              </Alert>
              {result.new_password ? (
                <div className="space-y-1.5">
                  <Label>One-time password (shown once — copy it now)</Label>
                  <div className="flex items-center gap-2">
                    <code className="flex-1 truncate rounded-md bg-muted px-3 py-2 font-mono text-sm">
                      {result.new_password}
                    </code>
                    <Button variant="outline" size="sm" onClick={copyPassword}>
                      {copied ? (
                        <Check className="h-4 w-4" />
                      ) : (
                        <Copy className="h-4 w-4" />
                      )}
                      {copied ? "Copied" : "Copy"}
                    </Button>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Deliver this to the Admin out of band. Wolf never stores or
                    shows it again.
                  </p>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">
                  An existing account was promoted to Admin — they keep their
                  current password.
                </p>
              )}
            </div>
          ) : alreadyHasAdmin ? (
            <Alert>
              <AlertTitle>This organization already has an active Admin</AlertTitle>
              <AlertDescription>
                Break-glass seeding only applies to an org with zero Admins.
                Adding more users is the org Admin&apos;s job (Phase 6.5-e).
              </AlertDescription>
            </Alert>
          ) : (
            <div className="max-w-md space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="admin-email">Admin email</Label>
                <Input
                  id="admin-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="admin@acme.example"
                  disabled={busy || (org !== null && !org.is_active)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="admin-name">Display name</Label>
                <Input
                  id="admin-name"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="Organization Admin"
                  disabled={busy || (org !== null && !org.is_active)}
                />
              </div>
              {formError ? (
                <p className="text-sm text-destructive">{formError}</p>
              ) : null}
              {org !== null && !org.is_active ? (
                <p className="text-sm text-muted-foreground">
                  This organization is soft-deleted — reactivate it before
                  seeding an Admin.
                </p>
              ) : null}
              <Button
                onClick={submitSeed}
                disabled={busy || (org !== null && !org.is_active)}
              >
                {busy ? "Creating…" : "Create initial Admin"}
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function BackLink() {
  return (
    <Link
      href="/superuser/organizations"
      className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft className="h-4 w-4" />
      All organizations
    </Link>
  );
}
