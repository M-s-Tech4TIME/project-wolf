"use client";

// Per-organization detail — Phase 6.5-d + 6.5-e.2.
//
// Two break-glass actions, both consent-gate-safe (no member roster is
// ever shown — ADR 0018):
//   - Seed the org's FIRST Admin via the recovery endpoint (works only
//     while the org has zero active Admins; 409 once an Admin exists —
//     routine user management is then the org Admin's job, Phase 6.5-e).
//   - Reset a member's password BY EMAIL (6.5-e.2) — the recovery path
//     for a locked-out sole Admin the org-scoped reset can't reach. The
//     Superuser types an email it already holds; no roster listing.

import { ArrowLeft, Check, Copy, KeyRound, Mail } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
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
import {
  ApiError,
  listOrganizations,
  recoverOrganizationAdmin,
  resetUserPasswordByEmail,
} from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { absoluteTimeTitle } from "@/lib/format";
import { isValidEmail } from "@/lib/utils";
import type {
  MemberPasswordReset,
  Organization,
  RecoveryAdminResponse,
} from "@/lib/types";

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
    if (!isValidEmail(trimmedEmail))
      return setFormError("Enter a valid email address.");
    if (displayName.trim().length > 255)
      return setFormError("Display name must be 255 characters or fewer.");
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

  // Break-glass reset-by-email (6.5-e.2)
  const [resetEmail, setResetEmail] = useState("");
  const [resetConfirm, setResetConfirm] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);
  const [resetResult, setResetResult] = useState<MemberPasswordReset | null>(null);
  const [resetCopied, setResetCopied] = useState(false);

  async function confirmResetByEmail() {
    setResetConfirm(false);
    setResetBusy(true);
    setResetError(null);
    try {
      const res = await resetUserPasswordByEmail(resetEmail.trim());
      setResetResult(res);
    } catch (e) {
      setResetError(
        e instanceof ApiError ? e.message : "Failed to reset password.",
      );
    } finally {
      setResetBusy(false);
    }
  }

  async function copyResetPassword() {
    if (!resetResult) return;
    if (await copyText(resetResult.new_password)) {
      setResetCopied(true);
      setTimeout(() => setResetCopied(false), 2000);
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

      <Card className="px-5">
        <CardHeader className="px-0">
          <CardTitle className="flex items-center gap-2 text-base">
            <Mail className="h-5 w-5" />
            Reset a member&apos;s password
          </CardTitle>
          <CardDescription>
            Break-glass recovery for a locked-out member (e.g. the only Admin,
            whom no peer can reset). Enter the member&apos;s email; their current
            password and sessions end, and you&apos;ll get a one-time password to
            share. Looks up by email install-wide — no organization data is shown.
          </CardDescription>
        </CardHeader>
        <CardContent className="px-0">
          {resetResult ? (
            <div className="space-y-3">
              <Alert>
                <AlertTitle>Password reset</AlertTitle>
                <AlertDescription>
                  New one-time password for{" "}
                  <span className="font-medium">{resetResult.email}</span>.
                </AlertDescription>
              </Alert>
              <div className="space-y-1.5">
                <Label>One-time password (shown once)</Label>
                <div className="flex items-center gap-2">
                  <code className="flex-1 truncate rounded-md bg-muted px-3 py-2 font-mono text-sm">
                    {resetResult.new_password}
                  </code>
                  <Button variant="outline" size="sm" onClick={copyResetPassword}>
                    {resetCopied ? (
                      <Check className="h-4 w-4" />
                    ) : (
                      <Copy className="h-4 w-4" />
                    )}
                    {resetCopied ? "Copied" : "Copy"}
                  </Button>
                </div>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setResetResult(null);
                  setResetEmail("");
                }}
              >
                Reset another
              </Button>
            </div>
          ) : (
            <div className="max-w-md space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="reset-email">Member email</Label>
                <Input
                  id="reset-email"
                  type="email"
                  value={resetEmail}
                  onChange={(e) => setResetEmail(e.target.value)}
                  placeholder="locked-out-admin@acme.example"
                  disabled={resetBusy}
                />
              </div>
              {resetError ? (
                <p className="text-sm text-destructive">{resetError}</p>
              ) : null}
              <Button
                variant="outline"
                onClick={() => {
                  setResetError(null);
                  const trimmed = resetEmail.trim();
                  if (!trimmed) setResetError("Email is required.");
                  else if (!isValidEmail(trimmed))
                    setResetError("Enter a valid email address.");
                  else setResetConfirm(true);
                }}
                disabled={resetBusy}
              >
                {resetBusy ? "Resetting…" : "Reset password"}
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={resetConfirm}
        title="Reset this member's password?"
        description={
          <>
            A new one-time password will be generated for{" "}
            <span className="font-medium">{resetEmail.trim()}</span>. Their
            current password stops working and any active sessions end. Deliver
            the new password out of band.
          </>
        }
        confirmLabel="Reset password"
        onConfirm={confirmResetByEmail}
        onCancel={() => setResetConfirm(false)}
      />
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
