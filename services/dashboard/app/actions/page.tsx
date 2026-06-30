"use client";

// Action-approval queue — Phase 6 6-b (ADR 0025, doc 04).
//
// Lists this org's pending action proposals (Wolf proposes; a human approves;
// only then does the gateway execute) with the full review context — action,
// target, Wolf's rationale, expected effect, grounding evidence, rollback plan,
// severity, requester, and TTL. A reviewer with ACTION_APPROVE can Approve
// (which runs the server-side freshness re-check → bounded write → verification
// read) or Reject. Separation of duties is enforced server-side: a requester
// can't approve their own proposal. A recent-activity history shows what was
// executed / failed / rejected. The backend is the authority on every action.

import { CheckCircle2, ClipboardList, Clock, ShieldAlert, XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useAuth } from "@/components/auth-provider";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ApiError,
  approveActionProposal,
  listActionProposals,
  rejectActionProposal,
} from "@/lib/api";
import { canApproveActions } from "@/lib/capabilities";
import { absoluteTimeTitle, relativeTime, timeUntil } from "@/lib/format";
import type { ActionProposal } from "@/lib/types";

// ── Field accessors (target/evidence/result are open JSON maps) ────────────

function agentIdOf(target: Record<string, unknown>): string | null {
  const v = target["agent_id"];
  return typeof v === "string" ? v : null;
}

function targetSummary(target: Record<string, unknown>): string {
  const agent = agentIdOf(target);
  if (agent) return `agent ${agent}`;
  // rule_tuning targets a rule id — read it cleanly ("rule 100700") rather
  // than dumping the raw JSON ({"rule_id":"100700"}).
  if (typeof target["rule_id"] === "string") return `rule ${target["rule_id"]}`;
  return JSON.stringify(target);
}

/** Human one-liner for the action's structured parameters (what it acts on).
 *  Reversal-aware: an undo reads "unblock IP …" / "re-enable user …", not the
 *  forward "block IP …" — so the TARGET matches what the action actually does. */
function paramsSummary(p: ActionProposal): string | null {
  const params = p.parameters;
  // agent_action group management — the action field (assign_group / remove_group)
  // already encodes the direction, so this reads accurately for undos too.
  if (p.action_class === "agent_action" && typeof params["group"] === "string") {
    const verb = p.action === "assign_group" ? "assign to group" : "remove from group";
    return `${verb} ${params["group"]}`;
  }
  // rule_tuning — the rule id is the target; the action encodes the operation
  // (disable_rule / adjust_level / restore_rules), so undos read accurately too.
  if (p.action_class === "rule_tuning") {
    const ruleId = typeof p.target["rule_id"] === "string" ? p.target["rule_id"] : "?";
    if (p.action === "restore_rules") return `restore rule ${ruleId}`;
    if (p.action === "disable_rule") return `disable rule ${ruleId}`;
    const lvl = typeof params["level"] === "number" ? params["level"] : "?";
    return `set rule ${ruleId} to level ${lvl}`;
  }
  const intent = typeof params["intent"] === "string" ? params["intent"] : "";
  const undo = isReversal(p);
  const parts: string[] = [];
  if (typeof params["srcip"] === "string") {
    const verb = intent === "unblock_ip" || undo ? "unblock IP" : "block IP";
    parts.push(`${verb} ${params["srcip"]}`);
  }
  if (typeof params["username"] === "string") {
    const verb = intent === "enable_user" || undo ? "re-enable user" : "disable user";
    parts.push(`${verb} ${params["username"]}`);
  }
  return parts.length ? parts.join(", ") : null;
}

function alertIdsOf(evidence: Record<string, unknown>): string[] {
  const v = evidence["alert_ids"];
  return Array.isArray(v) ? v.map(String) : [];
}

// ── Reversal helpers (slice 6-d, ADR 0028) ──────────────────────────────────

/** A reversal (unblock / re-enable) undoes a prior block. */
function isReversal(p: ActionProposal): boolean {
  return p.reverses_proposal_id !== null;
}

/** A system-initiated automatic reversal (a timed block expired). */
function isAutoReversal(p: ActionProposal): boolean {
  return p.parameters["auto"] === true;
}

function shortId(id: string): string {
  return id.slice(0, 8);
}

/** Humanize a block duration (seconds) → "30m" / "1h" / "2d". */
function formatDuration(parameters: Record<string, unknown>): string | null {
  const s = parameters["block_duration_seconds"];
  if (typeof s !== "number" || s <= 0) return null;
  if (s % 86400 === 0) return `${s / 86400}d`;
  if (s % 3600 === 0) return `${s / 3600}h`;
  if (s % 60 === 0) return `${s / 60}m`;
  return `${s}s`;
}

/** One-line human summary of the verification-read result (or failure). */
function resultDetail(result: Record<string, unknown> | null): string | null {
  if (!result) return null;
  if (typeof result["error"] === "string") return result["error"];
  if (typeof result["freshness"] === "string") return String(result["freshness"]);
  // A reversal authorises + records the undo; the host change is wolf-pack-bound.
  if (result["reversal_state"] === "authorized_pending_wolf_pack")
    return "Reversal authorised + recorded — physical removal pending wolf-pack";
  // rule_tuning (6-e.3): forward result carries override_written; the snapshot-
  // restore reverse carries override_removed. Surface the real apply evidence so
  // the approver can see the override landed (it was silently unrendered before).
  if (typeof result["override_written"] === "boolean") {
    const rid = result["rule_id"];
    const lvl = result["target_level"];
    return result["override_written"]
      ? `rule ${rid} → level ${lvl}: override written to local_rules.xml + ruleset validated + cluster restart issued (active ~15–30s after restart)`
      : `rule ${rid}: override did NOT persist — change not applied`;
  }
  if (typeof result["override_removed"] === "boolean") {
    const rid = result["rule_id"];
    return result["override_removed"]
      ? `rule ${rid} restored: override removed from local_rules.xml + validated + cluster restart issued`
      : `rule ${rid}: restore did NOT remove the override`;
  }
  const total = result["total_affected_items"];
  if (typeof total === "number") return `${total} target(s) affected`;
  return null;
}

// ── Badges ─────────────────────────────────────────────────────────────────

function severityBadge(severity: string) {
  // Three honest tiers: critical/high = red, medium = amber, low = muted.
  if (severity === "critical")
    return <Badge variant="destructive">Critical severity</Badge>;
  if (severity === "high") return <Badge variant="destructive">High severity</Badge>;
  if (severity === "medium")
    return (
      <Badge
        variant="outline"
        className="border-amber-400/50 bg-amber-400/15 text-amber-700 dark:text-amber-400"
      >
        Medium severity
      </Badge>
    );
  return <Badge variant="secondary" className="capitalize">{severity} severity</Badge>;
}

function stateBadge(state: ActionProposal["state"]) {
  switch (state) {
    case "pending":
      return <Badge variant="secondary">Pending</Badge>;
    case "succeeded":
      return <Badge>Executed</Badge>;
    case "failed":
      return <Badge variant="destructive">Failed</Badge>;
    case "rejected":
      return <Badge variant="outline">Rejected</Badge>;
    case "expired":
      return <Badge variant="outline">Expired</Badge>;
    default:
      // draft / approved / executing / rolled_back
      return <Badge variant="outline" className="capitalize">{state.replace("_", " ")}</Badge>;
  }
}

/** A chip marking a proposal as an undo (and whether it was automatic). */
function kindBadge(p: ActionProposal) {
  if (!isReversal(p)) return null;
  const label = isAutoReversal(p) ? "Auto-reversal" : "Reversal";
  return (
    <Badge
      variant="outline"
      className="border-sky-400/50 bg-sky-400/15 text-sky-700 dark:text-sky-400"
    >
      {label}
    </Badge>
  );
}

// The settled outcome of the most recent approve, shown as a banner.
type Outcome = { state: ActionProposal["state"]; action: string; detail: string | null };

export default function ActionsPage() {
  const { me } = useAuth();
  const canApprove = canApproveActions(me?.role);

  const [proposals, setProposals] = useState<ActionProposal[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [busy, setBusy] = useState(false);

  // Approve confirm + reject dialog targets.
  const [approving, setApproving] = useState<ActionProposal | null>(null);
  const [rejecting, setRejecting] = useState<ActionProposal | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  const load = useCallback(() => {
    // One fetch across all states; partition client-side (mirrors the access
    // page). Pending counts are small in practice, well within the cap.
    listActionProposals("all")
      .then(setProposals)
      .catch((e) =>
        setError(e instanceof ApiError ? e.message : "Failed to load action proposals."),
      );
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function confirmApprove() {
    if (!approving) return;
    const target = approving;
    setApproving(null);
    setBusy(true);
    setError(null);
    setOutcome(null);
    try {
      const settled = await approveActionProposal(target.id);
      setOutcome({
        state: settled.state,
        action: settled.action,
        detail: resultDetail(settled.result),
      });
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to approve the proposal.");
      load();
    } finally {
      setBusy(false);
    }
  }

  async function submitReject() {
    if (!rejecting) return;
    const target = rejecting;
    setRejecting(null);
    setBusy(true);
    setError(null);
    try {
      await rejectActionProposal(target.id, rejectReason.trim() || undefined);
      setRejectReason("");
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to reject the proposal.");
    } finally {
      setBusy(false);
    }
  }

  const pending = (proposals ?? []).filter((p) => p.state === "pending");
  const history = (proposals ?? []).filter((p) => p.state !== "pending");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <ClipboardList className="h-5 w-5" />
          Action approvals
        </h1>
        <p className="text-sm text-muted-foreground">
          Wolf proposes actions on your Wazuh fleet but never runs them on its
          own. Review each proposal&apos;s evidence and expected effect, then
          approve to execute or reject. Approving runs a freshness re-check and a
          verification read; you can&apos;t approve a proposal you raised
          yourself.
        </p>
      </div>

      {error ? (
        <Alert variant="destructive">
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {outcome ? (
        <Alert variant={outcome.state === "succeeded" ? "default" : "destructive"}>
          <AlertTitle className="flex items-center gap-2">
            {outcome.state === "succeeded" ? (
              <CheckCircle2 className="h-4 w-4" />
            ) : (
              <XCircle className="h-4 w-4" />
            )}
            {outcome.state === "succeeded"
              ? `Executed '${outcome.action}'`
              : outcome.state === "expired"
                ? `'${outcome.action}' was stale — not executed`
                : `'${outcome.action}' did not complete (${outcome.state})`}
          </AlertTitle>
          {outcome.detail ? <AlertDescription>{outcome.detail}</AlertDescription> : null}
        </Alert>
      ) : null}

      {/* Pending queue */}
      <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted-foreground">
          Awaiting approval{pending.length ? ` (${pending.length})` : ""}
        </h2>
        {proposals === null ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : pending.length === 0 ? (
          <p className="text-sm text-muted-foreground">No actions awaiting approval.</p>
        ) : (
          <div className="space-y-3">
            {pending.map((p) => {
              const ownProposal = !!me && p.requested_by === me.user_id;
              const alertIds = alertIdsOf(p.evidence);
              // timeUntil() returns "expired" once the TTL has passed.
              const ttl = timeUntil(p.expires_at);
              const expired = ttl === "expired";
              return (
                <Card key={p.id}>
                  <CardHeader>
                    <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                      <code className="rounded bg-muted px-1.5 py-0.5 text-sm">{p.action}</code>
                      <span className="font-normal text-muted-foreground">
                        on {targetSummary(p.target)}
                      </span>
                      <span className="ml-auto flex items-center gap-1.5">
                        {kindBadge(p)}
                        {severityBadge(p.severity)}
                      </span>
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2 text-sm">
                    {paramsSummary(p) ? (
                      <Field label="Target">{paramsSummary(p)}</Field>
                    ) : null}
                    {isReversal(p) && p.reverses_proposal_id ? (
                      <Field label="Undoes">
                        block{" "}
                        <code className="rounded bg-muted px-1 py-0.5 text-xs">
                          #{shortId(p.reverses_proposal_id)}
                        </code>
                        <span className="ml-1 text-xs text-muted-foreground">
                          — physical removal runs via wolf-pack
                        </span>
                      </Field>
                    ) : null}
                    {formatDuration(p.parameters) ? (
                      <Field label="Duration">
                        {formatDuration(p.parameters)}{" "}
                        <span className="text-xs text-muted-foreground">
                          — Wolf auto-reverses it when the window expires
                        </span>
                      </Field>
                    ) : null}
                    <Field label="Reason">{p.rationale}</Field>
                    {p.expected_effect ? (
                      <Field label="Expected effect">{p.expected_effect}</Field>
                    ) : null}
                    <Field label="Evidence">
                      {alertIds.length ? (
                        <span>
                          {alertIds.length} alert{alertIds.length === 1 ? "" : "s"}:{" "}
                          <span className="text-muted-foreground">{alertIds.join(", ")}</span>
                        </span>
                      ) : (
                        <span className="italic text-muted-foreground">None attached</span>
                      )}
                    </Field>
                    {p.rollback_plan ? (
                      <Field label="Rollback">{p.rollback_plan}</Field>
                    ) : null}
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-1 text-xs text-muted-foreground">
                      <span title={absoluteTimeTitle(p.created_at)}>
                        Proposed {relativeTime(p.created_at)}
                      </span>
                      <span
                        className={`flex items-center gap-1${expired ? " text-destructive" : ""}`}
                        title={absoluteTimeTitle(p.expires_at)}
                      >
                        <Clock className="h-3 w-3" />
                        {expired ? "Expired" : `Expires ${ttl}`}
                      </span>
                    </div>
                  </CardContent>
                  {canApprove ? (
                    <CardFooter className="justify-end gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setRejectReason("");
                          setRejecting(p);
                        }}
                        disabled={busy}
                      >
                        Reject
                      </Button>
                      <Button
                        size="sm"
                        onClick={() => setApproving(p)}
                        disabled={busy || ownProposal}
                        title={
                          ownProposal
                            ? "You can't approve a proposal you raised (separation of duties)"
                            : undefined
                        }
                      >
                        Approve &amp; execute
                      </Button>
                    </CardFooter>
                  ) : (
                    <CardFooter>
                      <p className="text-xs text-muted-foreground">
                        Your role can view proposals but not approve them.
                      </p>
                    </CardFooter>
                  )}
                </Card>
              );
            })}
          </div>
        )}
      </section>

      {/* Recent activity */}
      <section className="space-y-2">
        <h2 className="text-sm font-medium text-muted-foreground">Recent activity</h2>
        {history.length === 0 ? (
          <p className="text-sm text-muted-foreground">No past actions.</p>
        ) : (
          <ul className="space-y-2">
            {history.slice(0, 30).map((p) => {
              const detail = resultDetail(p.result);
              const when = p.executed_at ?? p.approved_at ?? p.created_at;
              return (
                <li
                  key={p.id}
                  className="rounded-xl px-3 py-2 text-sm ring-1 ring-foreground/10"
                >
                  {/* Header row: badges + action + target, with the
                      relative time pinned right. flex-wrap so a long target
                      drops to the next line instead of pushing past the box. */}
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    {stateBadge(p.state)}
                    {kindBadge(p)}
                    <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{p.action}</code>
                    <span className="min-w-0 break-words text-muted-foreground">
                      on {targetSummary(p.target)}
                    </span>
                    {/* A still-in-effect timed block shows when Wolf will auto-reverse it;
                        a block whose reversal is already authorised says so. */}
                    {!isReversal(p) && p.state === "succeeded" && p.auto_unblock_at && !p.reversal_proposal_id ? (
                      <span
                        className="text-xs text-muted-foreground"
                        title={absoluteTimeTitle(p.auto_unblock_at)}
                      >
                        · auto-reverses {timeUntil(p.auto_unblock_at)}
                      </span>
                    ) : null}
                    {!isReversal(p) && p.reversal_proposal_id ? (
                      <span className="text-xs text-sky-700 dark:text-sky-400">
                        · reversal authorised
                      </span>
                    ) : null}
                    <span
                      className="ml-auto shrink-0 text-xs text-muted-foreground"
                      title={absoluteTimeTitle(when)}
                    >
                      {relativeTime(when)}
                    </span>
                  </div>
                  {/* Detail (e.g. the rule_tuning evidence line) wraps fully
                      on its own line — no truncation, the whole content stays
                      visible inside the box. */}
                  {detail ? (
                    <p className="mt-1 break-words text-xs text-muted-foreground">
                      — {detail}
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* Approve confirm */}
      <ConfirmDialog
        open={approving !== null}
        title="Approve and execute this action?"
        variant="default"
        description={
          approving ? (
            isReversal(approving) ? (
              <>
                Wolf will authorise and record the reversal of{" "}
                <span className="font-medium">{approving.action}</span>
                {paramsSummary(approving) ? (
                  <> (<span className="font-medium">{paramsSummary(approving)}</span>)</>
                ) : null}{" "}
                on <span className="font-medium">{targetSummary(approving.target)}</span>. The
                physical removal runs on the host via wolf-pack (Phase 12) — the Wazuh API
                can&apos;t dispatch an active-response &ldquo;delete&rdquo;, so the block stays in
                effect until then.
              </>
            ) : (
              <>
                Wolf will run <span className="font-medium">{approving.action}</span>
                {paramsSummary(approving) ? (
                  <> (<span className="font-medium">{paramsSummary(approving)}</span>)</>
                ) : null}{" "}
                on <span className="font-medium">{targetSummary(approving.target)}</span> using this
                organization&apos;s Wazuh credential. This is a real change on your fleet. A
                freshness re-check runs first; if the world has moved, it won&apos;t execute.
              </>
            )
          ) : null
        }
        confirmLabel="Approve & execute"
        onConfirm={confirmApprove}
        onCancel={() => setApproving(null)}
      />

      {/* Reject dialog */}
      <Dialog open={rejecting !== null} onOpenChange={(o) => !busy && !o && setRejecting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <ShieldAlert className="h-4 w-4" />
              Reject proposal
            </DialogTitle>
            <DialogDescription>
              {rejecting ? (
                <>
                  Decline <span className="font-medium">{rejecting.action}</span> on{" "}
                  <span className="font-medium">{targetSummary(rejecting.target)}</span>. Nothing
                  executes. You can add an optional note for the audit trail.
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
              placeholder="Why this action is being declined"
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
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2">
      <span className="w-28 shrink-0 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className="flex-1">{children}</span>
    </div>
  );
}
