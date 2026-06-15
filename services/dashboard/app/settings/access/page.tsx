"use client";

// Superuser-access consent gate — Admin side (Phase 6.5-f).
//
// Admin-only (guard in app/settings/layout.tsx). Shows the install
// Superuser's access-requests for THIS org (pending first), lets the Admin
// approve (honouring or overriding the requested duration, or granting
// "until revoked") or reject, shows any current active grant with a
// Revoke-now control, and lists recent decisions. The backend is the
// authority: every action is org-scoped + dual-audited, and expiry is
// lazy server-side. ADR 0018 consent gate.

import { ChevronDown, Clock, ShieldAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ApiError,
  approveAccessRequest,
  fetchSuperuserAccess,
  listOrgAccessRequests,
  rejectAccessRequest,
  revokeSuperuserMembership,
} from "@/lib/api";
import { absoluteTimeTitle, relativeTime, timeUntil } from "@/lib/format";
import {
  ACCESS_DURATION_OPTIONS,
  type AccessApprove,
  type OrgAccessRequest,
  type SuperuserAccessGrant,
} from "@/lib/types";

// Approve-dialog duration choices: "Use requested" honours what the
// Superuser asked for; the rest override it. Keyed strings map to the
// AccessApprove body in `approveBody`.
const APPROVE_CHOICES: { key: string; label: string }[] = [
  { key: "requested", label: "Use requested duration" },
  ...ACCESS_DURATION_OPTIONS.map((o) => ({
    key: o.hours === null ? "until_revoked" : `h${o.hours}`,
    label: o.label,
  })),
];

function approveBody(key: string): AccessApprove {
  if (key === "requested") return { mode: "requested" };
  if (key === "until_revoked") return { mode: "until_revoked" };
  return { mode: "hours", duration_hours: Number(key.slice(1)) };
}

function durationLabel(hours: number | null): string {
  if (hours === null) return "until revoked";
  return `${hours}h`;
}

function statusBadge(status: string) {
  switch (status) {
    case "pending":
      return <Badge variant="secondary">Pending</Badge>;
    case "approved":
      return <Badge>Approved</Badge>;
    default:
      // rejected / cancelled / revoked / expired — terminal, muted.
      return <Badge variant="outline" className="capitalize">{status}</Badge>;
  }
}

/** The most recent event on a request (for the card's headline time). */
function latestEventAt(r: OrgAccessRequest): string {
  return r.ended_at ?? r.decided_at ?? r.requested_at;
}

type LifecycleStep = { key: string; label: string; when: string | null; detail?: string };

/** Expand a request row into its ordered lifecycle steps for the timeline:
 *  Requested → Approved/Rejected/Cancelled → Revoked/Expired. */
function lifecycleSteps(r: OrgAccessRequest): LifecycleStep[] {
  const steps: LifecycleStep[] = [
    {
      key: "requested",
      label: `Requested by ${r.superuser_display_name}`,
      when: r.requested_at,
      detail: r.reason
        ? `“${r.reason}” · asked for ${durationLabel(r.requested_duration_hours)}`
        : `Asked for ${durationLabel(r.requested_duration_hours)}`,
    },
  ];
  if (r.status === "cancelled") {
    steps.push({ key: "cancelled", label: "Cancelled by the Superuser", when: r.decided_at });
  } else if (r.status === "rejected") {
    steps.push({
      key: "rejected",
      label: r.decided_by_display_name ? `Rejected by ${r.decided_by_display_name}` : "Rejected",
      when: r.decided_at,
    });
  } else {
    // approved → (revoked | expired): all went through approval first.
    if (r.decided_at) {
      steps.push({
        key: "approved",
        label: r.decided_by_display_name
          ? `Approved by ${r.decided_by_display_name}`
          : "Approved",
        when: r.decided_at,
        detail: r.granted_expires_at
          ? `Granted until ${absoluteTimeTitle(r.granted_expires_at)}`
          : "Granted open-ended (until revoked)",
      });
    }
    if (r.status === "revoked") {
      steps.push({ key: "revoked", label: "Access revoked by an admin", when: r.ended_at });
    } else if (r.status === "expired") {
      steps.push({ key: "expired", label: "Access expired", when: r.ended_at });
    }
  }
  return steps;
}

export default function AccessPage() {
  const [requests, setRequests] = useState<OrgAccessRequest[] | null>(null);
  const [grant, setGrant] = useState<SuperuserAccessGrant | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Approve dialog
  const [approving, setApproving] = useState<OrgAccessRequest | null>(null);
  const [approveKey, setApproveKey] = useState("requested");
  // Reject dialog
  const [rejecting, setRejecting] = useState<OrgAccessRequest | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  // Revoke confirm
  const [revokeOpen, setRevokeOpen] = useState(false);

  const load = useCallback(() => {
    // Grant FIRST: that endpoint runs server-side lazy expiry, which also
    // flips a just-lapsed request to "expired" — so the request list we
    // fetch next reflects the terminal state on the same load (no stale
    // "approved" lingering after expiry). The grant card is best-effort;
    // the request list is primary, so only its failure surfaces an error.
    fetchSuperuserAccess()
      .then(setGrant)
      .catch(() => {
        /* grant card is best-effort */
      })
      .then(() => listOrgAccessRequests())
      .then(setRequests)
      .catch((e) =>
        setError(e instanceof ApiError ? e.message : "Failed to load access requests"),
      );
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function submitApprove() {
    if (!approving) return;
    setBusy(true);
    setError(null);
    try {
      await approveAccessRequest(approving.id, approveBody(approveKey));
      setApproving(null);
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to approve request.");
      setApproving(null);
    } finally {
      setBusy(false);
    }
  }

  async function submitReject() {
    if (!rejecting) return;
    setBusy(true);
    setError(null);
    try {
      await rejectAccessRequest(rejecting.id, rejectReason.trim() || undefined);
      setRejecting(null);
      setRejectReason("");
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to reject request.");
      setRejecting(null);
    } finally {
      setBusy(false);
    }
  }

  async function confirmRevoke() {
    setRevokeOpen(false);
    setBusy(true);
    setError(null);
    try {
      await revokeSuperuserMembership();
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to revoke access.");
    } finally {
      setBusy(false);
    }
  }

  const pending = (requests ?? []).filter((r) => r.status === "pending");
  const history = (requests ?? []).filter((r) => r.status !== "pending");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <ShieldAlert className="h-5 w-5" />
          Superuser access
        </h1>
        <p className="text-sm text-muted-foreground">
          The install operator (Wolf Superuser) has no access to this
          organization&apos;s data unless you grant it. Grants are time-limited
          and you can revoke at any time. Every member is shown a banner while a
          grant is active.
        </p>
      </div>

      {error ? (
        <Alert variant="destructive">
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {/* Current active grant */}
      {grant ? (
        <Alert>
          <AlertTitle className="flex items-center gap-2">
            <Clock className="h-4 w-4" />
            Superuser currently has access
          </AlertTitle>
          <AlertDescription>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span>
                {grant.granted_by_display_name
                  ? `Granted by ${grant.granted_by_display_name}. `
                  : ""}
                {grant.expires_at ? (
                  <>
                    Expires{" "}
                    <span title={absoluteTimeTitle(grant.expires_at)}>
                      {timeUntil(grant.expires_at)}
                    </span>
                    .
                  </>
                ) : (
                  "Open-ended — active until revoked."
                )}
              </span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setRevokeOpen(true)}
                disabled={busy}
              >
                Revoke now
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      ) : null}

      {/* Pending requests */}
      <section className="space-y-2">
        <h2 className="text-sm font-medium text-muted-foreground">
          Pending requests
        </h2>
        {requests === null ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : pending.length === 0 ? (
          <p className="text-sm text-muted-foreground">No pending requests.</p>
        ) : (
          <div className="rounded-xl ring-1 ring-foreground/10">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Requested by</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>Requested</TableHead>
                  <TableHead className="text-right">Decision</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pending.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="font-medium">
                      {r.superuser_display_name}
                      <div className="text-xs font-normal text-muted-foreground">
                        {r.superuser_email}
                      </div>
                    </TableCell>
                    <TableCell className="max-w-xs text-muted-foreground">
                      {r.reason ? (
                        <span className="line-clamp-2">{r.reason}</span>
                      ) : (
                        <span className="italic">No reason given</span>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {durationLabel(r.requested_duration_hours)}
                    </TableCell>
                    <TableCell
                      className="text-muted-foreground"
                      title={absoluteTimeTitle(r.requested_at)}
                    >
                      {relativeTime(r.requested_at)}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          size="sm"
                          onClick={() => {
                            setApproveKey("requested");
                            setApproving(r);
                          }}
                          disabled={busy || grant !== null}
                          title={
                            grant !== null
                              ? "Revoke the current grant before approving another"
                              : undefined
                          }
                        >
                          Approve
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setRejectReason("");
                            setRejecting(r);
                          }}
                          disabled={busy}
                        >
                          Reject
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </section>

      {/* Activity history — full per-request lifecycle timeline */}
      <section className="space-y-2">
        <h2 className="text-sm font-medium text-muted-foreground">
          Activity history
        </h2>
        {history.length === 0 ? (
          <p className="text-sm text-muted-foreground">No past requests.</p>
        ) : (
          <ul className="space-y-3">
            {history.slice(0, 20).map((r) => (
              <li key={r.id} className="rounded-xl p-3 ring-1 ring-foreground/10">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <span className="flex items-center gap-2">
                    {statusBadge(r.status)}
                    <span className="text-sm font-medium">
                      {r.superuser_display_name}
                    </span>
                  </span>
                  <span
                    className="shrink-0 text-xs text-muted-foreground"
                    title={absoluteTimeTitle(latestEventAt(r))}
                  >
                    {relativeTime(latestEventAt(r))}
                  </span>
                </div>
                <ol className="ml-1 space-y-2 border-l border-foreground/15 pl-4">
                  {lifecycleSteps(r).map((s) => (
                    <li key={s.key} className="relative text-sm">
                      <span className="absolute -left-[1.3125rem] top-1.5 h-2 w-2 rounded-full bg-foreground/30 ring-2 ring-background" />
                      <div className="flex items-baseline justify-between gap-3">
                        <span>{s.label}</span>
                        {s.when ? (
                          <span
                            className="shrink-0 text-xs text-muted-foreground"
                            title={absoluteTimeTitle(s.when)}
                          >
                            {relativeTime(s.when)}
                          </span>
                        ) : null}
                      </div>
                      {s.detail ? (
                        <p className="text-xs text-muted-foreground">{s.detail}</p>
                      ) : null}
                    </li>
                  ))}
                </ol>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Approve dialog */}
      <Dialog open={approving !== null} onOpenChange={(o) => !busy && !o && setApproving(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Grant Superuser access</DialogTitle>
            <DialogDescription>
              {approving ? (
                <>
                  <span className="font-medium">{approving.superuser_display_name}</span>{" "}
                  will gain read &amp; chat access to this organization
                  {approving.reason ? <> — “{approving.reason}”</> : null}. Choose
                  how long the grant lasts; you can revoke it sooner at any time.
                </>
              ) : null}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label>Duration</Label>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" className="w-full justify-between">
                  {APPROVE_CHOICES.find((c) => c.key === approveKey)?.label ??
                    "Use requested duration"}
                  <ChevronDown className="h-4 w-4 opacity-60" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent
                align="start"
                className="w-[--radix-dropdown-menu-trigger-width]"
              >
                <DropdownMenuRadioGroup value={approveKey} onValueChange={setApproveKey}>
                  {APPROVE_CHOICES.map((c) => (
                    <DropdownMenuRadioItem key={c.key} value={c.key}>
                      {c.label}
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>
            {approving ? (
              <p className="text-xs text-muted-foreground">
                Requested: {durationLabel(approving.requested_duration_hours)}.
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setApproving(null)} disabled={busy}>
              Cancel
            </Button>
            <Button onClick={submitApprove} disabled={busy}>
              {busy ? "Granting…" : "Grant access"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Reject dialog */}
      <Dialog open={rejecting !== null} onOpenChange={(o) => !busy && !o && setRejecting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject access request</DialogTitle>
            <DialogDescription>
              {rejecting ? (
                <>
                  Deny{" "}
                  <span className="font-medium">{rejecting.superuser_display_name}</span>
                  &apos;s request. No access is granted. You can add an optional
                  note for the audit trail.
                </>
              ) : null}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="reject-reason">Reason (optional)</Label>
            <Input
              id="reject-reason"
              value={rejectReason}
              maxLength={1000}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="Why this request is being declined"
            />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRejecting(null)} disabled={busy}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={submitReject} disabled={busy}>
              {busy ? "Rejecting…" : "Reject"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Revoke confirm */}
      <ConfirmDialog
        open={revokeOpen}
        title="Revoke Superuser access?"
        variant="destructive"
        description={
          <>
            The Superuser will immediately lose access to this organization&apos;s
            data. Members&apos; access banner clears. You can grant access again
            later if needed.
          </>
        }
        confirmLabel="Revoke access"
        onConfirm={confirmRevoke}
        onCancel={() => setRevokeOpen(false)}
      />
    </div>
  );
}
