"use client";

import { Building2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useAuth } from "@/components/auth-provider";
import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { ApiError, login, selectOrganization } from "@/lib/api";
import type { MembershipInfo } from "@/lib/types";

/**
 * Login form — Phase 6.5-c-ii (ADR 0018 §login UX).
 *
 * Email + password ONLY; no organization field. The backend's
 * three-shape response drives what happens next:
 *   - Superuser           → /superuser/dashboard (install-admin surface)
 *   - one membership      → org auto-selected → /chat
 *   - several memberships → inline org picker below, then /chat
 * The picked org becomes this TAB's context (sessionStorage) and rides
 * on every API call as the X-Organization-Id header.
 */
export function LoginForm() {
  const router = useRouter();
  const { refresh, setActiveOrganization } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Non-null once login returned needs_org_selection: the same card
  // swaps to the org picker (the session cookie is already issued).
  const [pendingMemberships, setPendingMemberships] = useState<MembershipInfo[] | null>(null);

  async function enterOrganization(organizationId: string) {
    setActiveOrganization(organizationId);
    try {
      // Audit-only (ADR 0018): routing is header-driven; a failure here
      // must never block the login itself.
      await selectOrganization(organizationId);
    } catch (err) {
      console.warn("select-organization audit call failed", err);
    }
    await refresh();
    router.push("/chat");
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const res = await login({ email, password });

      if (res.is_superuser) {
        // Install-admin session: org-less by design (consent gate).
        setActiveOrganization(null);
        await refresh();
        router.push(res.redirect ?? "/superuser/dashboard");
        return;
      }

      if (res.auto_selected_organization) {
        await enterOrganization(res.auto_selected_organization.organization_id);
        return;
      }

      if (res.needs_org_selection && res.memberships) {
        setPendingMemberships(res.memberships);
        return;
      }

      // Defensive: an unrecognized response shape is a real bug —
      // surface it rather than navigating somewhere half-authenticated.
      setError("Unexpected login response — please report this.");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Login failed");
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (pendingMemberships) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Choose an organization</CardTitle>
          <CardDescription>
            You belong to {pendingMemberships.length} organizations. This choice
            applies to the current tab — other tabs can work in a different one.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {pendingMemberships.map((m) => (
            <Button
              key={m.organization_id}
              variant="outline"
              className="w-full justify-start gap-3"
              onClick={() => void enterOrganization(m.organization_id)}
            >
              <Building2 className="h-4 w-4 shrink-0" />
              <span className="truncate">{m.organization_name}</span>
              <span className="ml-auto text-xs text-muted-foreground">{m.role}</span>
            </Button>
          ))}
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sign in</CardTitle>
        <CardDescription>
          Use your local account.  OIDC arrives in a later phase.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="email">Email or username</Label>
            {/* type="text" (not "email"): the fixed Superuser username
                "Wolf" must be enterable — ADR 0018, Phase 6.5-a. */}
            <Input
              id="email"
              type="text"
              required
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          <Button type="submit" className="w-full" disabled={submitting}>
            {submitting ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
