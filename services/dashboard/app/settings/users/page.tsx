"use client";

// Per-org user management — Phase 6.5-e.
//
// Admin-only (guard in app/settings/layout.tsx). Lists the active org's
// members, adds/removes members, changes roles, and shows recent member-change
// audit events. All backend endpoints are org-scoped (6.5-b); the active-org
// header rides on every call via apiFetch. The backend's Last-Admin guard
// (409) is the hard stop — the UI surfaces its message rather than
// reimplementing the rule.

import { Check, ChevronDown, Copy, KeyRound, Plus, Trash2, Users } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useAuth } from "@/components/auth-provider";
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
  changeMemberRole,
  createMember,
  fetchOrgAudit,
  listMembers,
  removeMember,
  resetMemberPassword,
} from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { absoluteTimeTitle, relativeTime } from "@/lib/format";
import {
  ORG_ROLES,
  type Member,
  type MemberPasswordReset,
  type OrgAuditEvent,
} from "@/lib/types";

function summarizeMemberEvent(e: OrgAuditEvent): string {
  const d = e.event_data ?? {};
  const email = typeof d.member_email === "string" ? d.member_email : "a member";
  switch (e.event_type) {
    case "organization.member.added":
      return `Added ${email}${typeof d.role === "string" ? ` as ${d.role}` : ""}`;
    case "organization.member.role_changed":
      return `Changed ${email}: ${String(d.old_role ?? "?")} → ${String(d.new_role ?? "?")}`;
    case "organization.member.removed":
      return `Removed ${email}`;
    default:
      return e.event_type;
  }
}

export default function UsersPage() {
  const { me } = useAuth();
  const [members, setMembers] = useState<Member[] | null>(null);
  const [recent, setRecent] = useState<OrgAuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Add-member dialog
  const [addOpen, setAddOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState<string>("analyst");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [createdPassword, setCreatedPassword] = useState<string | null>(null);
  const [createdEmail, setCreatedEmail] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // Per-row role-change in flight
  const [roleBusyId, setRoleBusyId] = useState<string | null>(null);
  // Remove confirm
  const [removing, setRemoving] = useState<Member | null>(null);
  // Password reset: confirm step → result reveal
  const [resetting, setResetting] = useState<Member | null>(null);
  const [resetResult, setResetResult] = useState<MemberPasswordReset | null>(null);
  const [resetCopied, setResetCopied] = useState(false);

  const load = useCallback(() => {
    listMembers()
      .then(setMembers)
      .catch((e) =>
        setError(e instanceof ApiError ? e.message : "Failed to load members"),
      );
    fetchOrgAudit(50, 0)
      .then((page) =>
        setRecent(
          page.events.filter((ev) =>
            ev.event_type.startsWith("organization.member."),
          ),
        ),
      )
      .catch(() => {
        /* audit panel is best-effort; member list is the primary surface */
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function openAdd() {
    setEmail("");
    setDisplayName("");
    setRole("analyst");
    setFormError(null);
    setCreatedPassword(null);
    setCreatedEmail(null);
    setAddOpen(true);
  }

  async function submitAdd() {
    const trimmedEmail = email.trim();
    const trimmedName = displayName.trim();
    if (!trimmedEmail) return setFormError("Email is required.");
    if (!trimmedName) return setFormError("Display name is required.");
    setBusy(true);
    setFormError(null);
    try {
      const res = await createMember({
        email: trimmedEmail,
        display_name: trimmedName,
        role,
      });
      load();
      if (res.new_password) {
        setCreatedEmail(res.email);
        setCreatedPassword(res.new_password);
      } else {
        setAddOpen(false);
      }
    } catch (e) {
      setFormError(e instanceof ApiError ? e.message : "Failed to add member.");
    } finally {
      setBusy(false);
    }
  }

  async function onRoleChange(member: Member, nextRole: string) {
    if (nextRole === member.role) return;
    setRoleBusyId(member.user_id);
    setError(null);
    try {
      await changeMemberRole(member.user_id, nextRole);
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to change role.");
    } finally {
      setRoleBusyId(null);
    }
  }

  async function confirmRemove() {
    if (!removing) return;
    setBusy(true);
    try {
      await removeMember(removing.user_id);
      setRemoving(null);
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to remove member.");
      setRemoving(null);
    } finally {
      setBusy(false);
    }
  }

  async function copyPassword() {
    if (!createdPassword) return;
    if (await copyText(createdPassword)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }

  async function confirmReset() {
    if (!resetting) return;
    setBusy(true);
    setError(null);
    try {
      const res = await resetMemberPassword(resetting.user_id);
      setResetting(null);
      setResetResult(res);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to reset password.");
      setResetting(null);
    } finally {
      setBusy(false);
    }
  }

  async function copyResetPassword() {
    if (!resetResult) return;
    if (await copyText(resetResult.new_password)) {
      setResetCopied(true);
      setTimeout(() => setResetCopied(false), 2000);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <Users className="h-5 w-5" />
            Users
          </h1>
          <p className="text-sm text-muted-foreground">
            Manage this organization&apos;s members and their roles.
          </p>
        </div>
        <Button onClick={openAdd}>
          <Plus className="h-4 w-4" />
          Add member
        </Button>
      </div>

      {error ? (
        <Alert variant="destructive">
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      <div className="rounded-xl ring-1 ring-foreground/10">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>Role</TableHead>
              <TableHead>Member since</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {members === null ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center text-muted-foreground">
                  Loading…
                </TableCell>
              </TableRow>
            ) : members.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center text-muted-foreground">
                  No members yet.
                </TableCell>
              </TableRow>
            ) : (
              members.map((m) => {
                const isSelf = m.user_id === me?.user_id;
                return (
                  <TableRow key={m.user_id}>
                    <TableCell className="font-medium">
                      {m.display_name}
                      {isSelf ? (
                        <span className="ml-1 text-xs text-muted-foreground">(you)</span>
                      ) : null}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{m.email}</TableCell>
                    <TableCell>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            variant="outline"
                            size="sm"
                            className="capitalize"
                            disabled={roleBusyId === m.user_id}
                          >
                            {m.role}
                            <ChevronDown className="h-3.5 w-3.5 opacity-60" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="start">
                          <DropdownMenuRadioGroup
                            value={m.role}
                            onValueChange={(v) => void onRoleChange(m, v)}
                          >
                            {ORG_ROLES.map((r) => (
                              <DropdownMenuRadioItem
                                key={r}
                                value={r}
                                className="capitalize"
                              >
                                {r}
                              </DropdownMenuRadioItem>
                            ))}
                          </DropdownMenuRadioGroup>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                    <TableCell
                      className="text-muted-foreground"
                      title={absoluteTimeTitle(m.member_since)}
                    >
                      {relativeTime(m.member_since)}
                    </TableCell>
                    <TableCell>
                      {m.is_active ? (
                        <Badge variant="secondary">Active</Badge>
                      ) : (
                        <Badge variant="outline">Inactive</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setResetting(m)}
                          title="Reset password"
                        >
                          <KeyRound className="h-4 w-4" />
                          <span className="sr-only">Reset password</span>
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setRemoving(m)}
                          title="Remove from organization"
                        >
                          <Trash2 className="h-4 w-4" />
                          <span className="sr-only">Remove</span>
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </div>

      {/* Recent member changes (audit) */}
      <section className="space-y-2">
        <h2 className="text-sm font-medium text-muted-foreground">
          Recent member changes
        </h2>
        {recent.length === 0 ? (
          <p className="text-sm text-muted-foreground">No recent changes.</p>
        ) : (
          <ul className="divide-y divide-foreground/10 rounded-xl ring-1 ring-foreground/10">
            {recent.slice(0, 10).map((e) => (
              <li
                key={e.id}
                className="flex items-center justify-between gap-4 px-3 py-2 text-sm"
              >
                <span>{summarizeMemberEvent(e)}</span>
                <span
                  className="shrink-0 text-xs text-muted-foreground"
                  title={absoluteTimeTitle(e.created_at)}
                >
                  {relativeTime(e.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Add member */}
      <Dialog open={addOpen} onOpenChange={(o) => !busy && setAddOpen(o)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add member</DialogTitle>
            <DialogDescription>
              Add an existing user by email, or create a new account. New
              accounts get a one-time password shown once.
            </DialogDescription>
          </DialogHeader>

          {createdPassword ? (
            <div className="space-y-3">
              <Alert>
                <AlertTitle>Member added</AlertTitle>
                <AlertDescription>
                  <span className="font-medium">{createdEmail}</span> can now sign
                  in.
                </AlertDescription>
              </Alert>
              <div className="space-y-1.5">
                <Label>One-time password (shown once — copy it now)</Label>
                <div className="flex items-center gap-2">
                  <code className="flex-1 truncate rounded-md bg-muted px-3 py-2 font-mono text-sm">
                    {createdPassword}
                  </code>
                  <Button variant="outline" size="sm" onClick={copyPassword}>
                    {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                    {copied ? "Copied" : "Copy"}
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Deliver this out of band. Wolf never shows it again.
                </p>
              </div>
              <DialogFooter>
                <Button onClick={() => setAddOpen(false)}>Done</Button>
              </DialogFooter>
            </div>
          ) : (
            <>
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="member-email">Email</Label>
                  <Input
                    id="member-email"
                    type="email"
                    value={email}
                    autoFocus
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="analyst@acme.example"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="member-name">Display name</Label>
                  <Input
                    id="member-name"
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    placeholder="Jane Analyst"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>Role</Label>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="outline" className="w-full justify-between capitalize">
                        {role}
                        <ChevronDown className="h-4 w-4 opacity-60" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="start" className="w-[--radix-dropdown-menu-trigger-width]">
                      <DropdownMenuRadioGroup value={role} onValueChange={setRole}>
                        {ORG_ROLES.map((r) => (
                          <DropdownMenuRadioItem key={r} value={r} className="capitalize">
                            {r}
                          </DropdownMenuRadioItem>
                        ))}
                      </DropdownMenuRadioGroup>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
                {formError ? (
                  <p className="text-sm text-destructive">{formError}</p>
                ) : null}
              </div>
              <DialogFooter>
                <Button variant="ghost" onClick={() => setAddOpen(false)} disabled={busy}>
                  Cancel
                </Button>
                <Button onClick={submitAdd} disabled={busy}>
                  {busy ? "Adding…" : "Add member"}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* Remove member */}
      <ConfirmDialog
        open={removing !== null}
        title="Remove member?"
        variant="destructive"
        description={
          <>
            <span className="font-medium">{removing?.display_name}</span> (
            {removing?.email}) will lose access to this organization.
            {removing?.user_id === me?.user_id
              ? " This is your own account — you will lose access."
              : ""}{" "}
            Their user account and the audit trail are retained.
          </>
        }
        confirmLabel="Remove"
        onConfirm={confirmRemove}
        onCancel={() => setRemoving(null)}
      />

      {/* Reset password — confirm */}
      <ConfirmDialog
        open={resetting !== null}
        title="Reset password?"
        description={
          <>
            A new one-time password will be generated for{" "}
            <span className="font-medium">{resetting?.display_name}</span> (
            {resetting?.email}). Their current password stops working and any
            active sessions end. You&apos;ll get the new password to share with
            them.
            {resetting?.user_id === me?.user_id
              ? " This is your own account — you will be signed out."
              : ""}
          </>
        }
        confirmLabel="Reset password"
        onConfirm={confirmReset}
        onCancel={() => setResetting(null)}
      />

      {/* Reset password — one-time reveal */}
      <Dialog
        open={resetResult !== null}
        onOpenChange={(o) => !o && setResetResult(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Password reset</DialogTitle>
            <DialogDescription>
              Share this one-time password with{" "}
              <span className="font-medium">{resetResult?.email}</span> out of
              band. Wolf never shows it again.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label>New password</Label>
            <div className="flex items-center gap-2">
              <code className="flex-1 truncate rounded-md bg-muted px-3 py-2 font-mono text-sm">
                {resetResult?.new_password}
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
          <DialogFooter>
            <Button onClick={() => setResetResult(null)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
