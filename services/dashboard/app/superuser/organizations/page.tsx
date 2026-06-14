"use client";

// Organizations management — Phase 6.5-d.
//
// Superuser-only install-scoped CRUD over GET/POST/PATCH/DELETE
// /api/v1/organizations. Delete is a soft-delete (is_active=false): the
// row + its audit trail survive, so we show deleted orgs with a badge
// rather than hiding them.

import { Building2, Pencil, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
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
  createOrganization,
  deleteOrganization,
  listOrganizations,
  updateOrganization,
} from "@/lib/api";
import { absoluteTimeTitle, relativeTime } from "@/lib/format";
import type { Organization } from "@/lib/types";

const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/;

/** Best-effort slug suggestion from a name; the user can still edit it. */
function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);
}

export default function OrganizationsPage() {
  const [orgs, setOrgs] = useState<Organization[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Create dialog
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newSlug, setNewSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);

  // Rename dialog
  const [renaming, setRenaming] = useState<Organization | null>(null);
  const [renameValue, setRenameValue] = useState("");

  // Delete confirm
  const [deleting, setDeleting] = useState<Organization | null>(null);

  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const load = useCallback(() => {
    listOrganizations()
      .then(setOrgs)
      .catch((e) =>
        setLoadError(e instanceof ApiError ? e.message : "Failed to load organizations"),
      );
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function openCreate() {
    setNewName("");
    setNewSlug("");
    setSlugTouched(false);
    setFormError(null);
    setCreateOpen(true);
  }

  async function submitCreate() {
    const name = newName.trim();
    const slug = (slugTouched ? newSlug : slugify(newName)).trim();
    if (!name) return setFormError("Name is required.");
    if (!SLUG_RE.test(slug)) {
      return setFormError(
        "Slug must be lowercase letters, digits, and hyphens (not leading/trailing).",
      );
    }
    setBusy(true);
    setFormError(null);
    try {
      await createOrganization({ name, slug });
      setCreateOpen(false);
      load();
    } catch (e) {
      setFormError(e instanceof ApiError ? e.message : "Failed to create organization.");
    } finally {
      setBusy(false);
    }
  }

  function openRename(org: Organization) {
    setRenaming(org);
    setRenameValue(org.name);
    setFormError(null);
  }

  async function submitRename() {
    if (!renaming) return;
    const name = renameValue.trim();
    if (!name) return setFormError("Name is required.");
    setBusy(true);
    setFormError(null);
    try {
      await updateOrganization(renaming.id, { name });
      setRenaming(null);
      load();
    } catch (e) {
      setFormError(e instanceof ApiError ? e.message : "Failed to rename organization.");
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete() {
    if (!deleting) return;
    setBusy(true);
    try {
      await deleteOrganization(deleting.id);
      setDeleting(null);
      load();
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : "Failed to delete organization.");
      setDeleting(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <Building2 className="h-5 w-5" />
            Organizations
          </h1>
          <p className="text-sm text-muted-foreground">
            Create, rename, and soft-delete organizations. Slugs are permanent.
          </p>
        </div>
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4" />
          New organization
        </Button>
      </div>

      {loadError ? (
        <Alert variant="destructive">
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      <div className="rounded-xl ring-1 ring-foreground/10">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Slug</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Created</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {orgs === null ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-muted-foreground">
                  Loading…
                </TableCell>
              </TableRow>
            ) : orgs.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-muted-foreground">
                  No organizations yet. Create the first one to get started.
                </TableCell>
              </TableRow>
            ) : (
              orgs.map((org) => (
                <TableRow key={org.id}>
                  <TableCell className="font-medium">
                    <Link
                      href={`/superuser/organizations/${org.id}`}
                      className="hover:underline"
                    >
                      {org.name}
                    </Link>
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {org.slug}
                  </TableCell>
                  <TableCell>
                    {org.is_active ? (
                      <Badge variant="secondary">Active</Badge>
                    ) : (
                      <Badge variant="outline">Deleted</Badge>
                    )}
                  </TableCell>
                  <TableCell
                    className="text-muted-foreground"
                    title={absoluteTimeTitle(org.created_at)}
                  >
                    {relativeTime(org.created_at)}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => openRename(org)}
                        disabled={!org.is_active}
                        title={org.is_active ? "Rename" : "Deleted orgs can't be renamed"}
                      >
                        <Pencil className="h-4 w-4" />
                        <span className="sr-only">Rename</span>
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setDeleting(org)}
                        disabled={!org.is_active}
                        title={org.is_active ? "Delete" : "Already deleted"}
                      >
                        <Trash2 className="h-4 w-4" />
                        <span className="sr-only">Delete</span>
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* Create */}
      <Dialog open={createOpen} onOpenChange={(o) => !busy && setCreateOpen(o)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New organization</DialogTitle>
            <DialogDescription>
              The slug is the permanent isolation key — it can never be changed.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="org-name">Name</Label>
              <Input
                id="org-name"
                value={newName}
                autoFocus
                onChange={(e) => setNewName(e.target.value)}
                placeholder="Acme Security"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="org-slug">Slug</Label>
              <Input
                id="org-slug"
                value={slugTouched ? newSlug : slugify(newName)}
                onChange={(e) => {
                  setSlugTouched(true);
                  setNewSlug(e.target.value);
                }}
                placeholder="acme-security"
                className="font-mono"
              />
              <p className="text-xs text-muted-foreground">
                Lowercase letters, digits, hyphens. Auto-filled from the name.
              </p>
            </div>
            {formError ? (
              <p className="text-sm text-destructive">{formError}</p>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setCreateOpen(false)} disabled={busy}>
              Cancel
            </Button>
            <Button onClick={submitCreate} disabled={busy}>
              {busy ? "Creating…" : "Create"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Rename */}
      <Dialog open={renaming !== null} onOpenChange={(o) => !busy && !o && setRenaming(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename organization</DialogTitle>
            <DialogDescription>
              The slug{" "}
              <span className="font-mono">{renaming?.slug}</span> stays the same.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="rename-input">Name</Label>
              <Input
                id="rename-input"
                value={renameValue}
                autoFocus
                onChange={(e) => setRenameValue(e.target.value)}
              />
            </div>
            {formError ? (
              <p className="text-sm text-destructive">{formError}</p>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRenaming(null)} disabled={busy}>
              Cancel
            </Button>
            <Button onClick={submitRename} disabled={busy}>
              {busy ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete */}
      <ConfirmDialog
        open={deleting !== null}
        title="Delete organization?"
        variant="destructive"
        description={
          <>
            <span className="font-medium">{deleting?.name}</span> will be
            soft-deleted: its users, audit trail, and data are retained, but it
            disappears from active use. This can be reversed in the database if
            needed.
          </>
        }
        confirmLabel="Delete"
        onConfirm={confirmDelete}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}
