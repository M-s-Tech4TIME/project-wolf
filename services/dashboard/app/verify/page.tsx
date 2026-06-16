"use client";

import { ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

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
import { ApiError, verifyInvite } from "@/lib/api";

/**
 * Invite-link verification screen — Phase 6.5-h (ADR 0018 item 9).
 *
 * An Admin-created account is "unverified" until the user pastes the invite
 * link their Admin delivered out of band. The user is already logged in
 * (credentials); this screen consumes the token to flip them to "verified",
 * after which the verification gate (organization/context.py) stops blocking
 * org data. Superusers and already-verified users never land here — the
 * routing in app/page.tsx + chat/layout.tsx sends them straight on.
 */

/** Accept either a full invite link or a bare token. Pull the token out of a
 *  pasted URL's `?token=` query or `#token=` fragment; otherwise treat the
 *  whole (trimmed) string as the token. */
function extractToken(pasted: string): string {
  const trimmed = pasted.trim();
  try {
    const url = new URL(trimmed);
    const fromQuery = url.searchParams.get("token");
    if (fromQuery) return fromQuery;
    const fromHash = new URLSearchParams(url.hash.replace(/^#/, "")).get("token");
    if (fromHash) return fromHash;
  } catch {
    /* not a URL — fall through and use the raw string as the token */
  }
  return trimmed;
}

export default function VerifyPage() {
  const router = useRouter();
  const { isLoading, me, refresh } = useAuth();
  const [value, setValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Anyone who doesn't belong here is redirected: unauthenticated → login;
  // already-verified or Superuser → chat (the gate doesn't apply to them).
  const done = me && (me.verification_status === "verified" || me.role === "superuser");
  useEffect(() => {
    if (isLoading) return;
    if (!me) {
      router.replace("/login");
    } else if (done) {
      router.replace("/chat");
    }
  }, [isLoading, me, done, router]);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const token = extractToken(value);
    if (!token) {
      setError("Paste the invitation link your administrator sent you.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await verifyInvite(token);
      await refresh();
      router.push("/chat");
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Verification failed — please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (isLoading || !me || done) {
    return (
      <div className="flex h-screen items-center justify-center text-muted-foreground">
        Loading…
      </div>
    );
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted/30 px-4">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <h1 className="text-3xl font-semibold tracking-tight">Wolf</h1>
          <p className="mt-1 text-sm text-muted-foreground">One more step to get started</p>
        </div>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck className="h-5 w-5" />
              Verify your account
            </CardTitle>
            <CardDescription>
              Paste the invitation link your administrator sent you. This
              confirms your account before you can access your organization.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4" noValidate>
              <div className="space-y-2">
                <Label htmlFor="invite">Invitation link</Label>
                <Input
                  id="invite"
                  type="text"
                  autoFocus
                  placeholder="Paste your invitation link here"
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                />
              </div>

              {error ? (
                <Alert variant="destructive" className="border-0 bg-transparent px-0 py-0">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              ) : null}

              <Button type="submit" className="w-full" disabled={submitting}>
                {submitting ? "Verifying…" : "Verify"}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
