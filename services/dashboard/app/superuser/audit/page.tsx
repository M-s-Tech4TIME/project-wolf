"use client";

// Install-wide audit log — Phase 6.5-d.
//
// GET /api/v1/superuser/audit: every organization's events plus
// system-level rows (organization_id IS NULL, shown as "System"),
// newest first, paginated. Distinct from the per-org audit view.

import { ChevronLeft, ChevronRight, ScrollText } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError, fetchInstallAudit } from "@/lib/api";
import { absoluteTimeTitle, relativeTime } from "@/lib/format";
import type { InstallAuditEvent } from "@/lib/types";

const PAGE_SIZE = 50;

export default function InstallAuditPage() {
  const [events, setEvents] = useState<InstallAuditEvent[] | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Used by the Prev/Next buttons (event handlers) — setting `loading`
  // synchronously here is fine outside an effect.
  const load = useCallback((nextOffset: number) => {
    setLoading(true);
    fetchInstallAudit(PAGE_SIZE, nextOffset)
      .then((page) => {
        setEvents(page.events);
        setOffset(page.offset);
        setError(null);
      })
      .catch((e) =>
        setError(e instanceof ApiError ? e.message : "Failed to load audit log"),
      )
      .finally(() => setLoading(false));
  }, []);

  // Initial load: state is set only inside the promise (no synchronous
  // setState in the effect body), and a cancel flag avoids a late write
  // after unmount.
  useEffect(() => {
    let cancelled = false;
    fetchInstallAudit(PAGE_SIZE, 0)
      .then((page) => {
        if (cancelled) return;
        setEvents(page.events);
        setOffset(page.offset);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.message : "Failed to load audit log");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // We fetch PAGE_SIZE rows; a full page implies there may be more.
  const hasNext = events !== null && events.length === PAGE_SIZE;
  const hasPrev = offset > 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <ScrollText className="h-5 w-5" />
          Install-wide audit log
        </h1>
        <p className="text-sm text-muted-foreground">
          Every organization&apos;s events plus system-level activity, newest
          first.
        </p>
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
              <TableHead>Time</TableHead>
              <TableHead>Organization</TableHead>
              <TableHead>Event</TableHead>
              <TableHead>User</TableHead>
              <TableHead>Source IP</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {events === null ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-muted-foreground">
                  Loading…
                </TableCell>
              </TableRow>
            ) : events.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-muted-foreground">
                  {offset === 0 ? "No audit events yet." : "No more events."}
                </TableCell>
              </TableRow>
            ) : (
              events.map((e) => (
                <TableRow key={e.id}>
                  <TableCell
                    className="whitespace-nowrap text-muted-foreground"
                    title={absoluteTimeTitle(e.created_at)}
                  >
                    {relativeTime(e.created_at)}
                  </TableCell>
                  <TableCell>
                    {e.organization_name ? (
                      e.organization_name
                    ) : (
                      <Badge variant="outline">System</Badge>
                    )}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{e.event_type}</TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {e.user_id ? `${e.user_id.slice(0, 8)}…` : "—"}
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {e.source_ip ?? "—"}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {events !== null
            ? `Showing ${events.length === 0 ? 0 : offset + 1}–${offset + events.length}`
            : ""}
        </p>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!hasPrev || loading}
            onClick={() => load(Math.max(0, offset - PAGE_SIZE))}
          >
            <ChevronLeft className="h-4 w-4" />
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!hasNext || loading}
            onClick={() => load(offset + PAGE_SIZE)}
          >
            Next
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
