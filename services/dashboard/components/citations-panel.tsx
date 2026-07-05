"use client";

import { FileSearch, FileText, Globe } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import type { Citation, ToolEvent } from "@/lib/types";

/** Docs-first tier → badge label (ADR 0032 A4/A5). Official Wazuh sources
 *  are visually distinguished from community ones — a trust signal. */
const SOURCE_LABEL: Record<string, string> = {
  official_docs: "Official docs",
  official: "Official",
  official_github: "Official GitHub",
  community: "Community",
};

function isOfficialSource(source: string | null | undefined): boolean {
  return source != null && source !== "community";
}

type Props = {
  citations: Citation[];
  toolEvents: ToolEvent[];
};

export function CitationsPanel({ citations, toolEvents }: Props) {
  const empty = citations.length === 0 && toolEvents.length === 0;

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border px-4 py-3">
        <h2 className="flex items-center gap-2 text-sm font-medium">
          <FileSearch className="h-4 w-4" /> Evidence
        </h2>
        <p className="mt-0.5 text-[10px] text-muted-foreground">
          Tool calls and citations behind the answer.
        </p>
      </div>
      {/* Native scroll container. Radix's ScrollArea introduces a nested
          viewport that doesn't reliably constrain inside our flex chain
          (same issue MessageThread hit — see its note), so a long evidence
          list overflowed past the viewport instead of scrolling. A native
          `min-h-0 flex-1 overflow-y-auto` is rock-solid. */}
      <div className="min-h-0 flex-1 overflow-y-auto [scrollbar-gutter:stable] [&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-foreground/30 hover:[&::-webkit-scrollbar-thumb]:bg-foreground/50">
        <div className="space-y-4 p-4">
          {empty ? (
            <p className="text-xs text-muted-foreground">
              No tool calls yet. Citations will appear here once the agent
              runs a tool.
            </p>
          ) : null}

          {toolEvents.length > 0 ? (
            <section>
              <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                Tool calls ({toolEvents.length})
              </h3>
              <ul className="space-y-2">
                {toolEvents.map((te) => (
                  <li
                    key={te.tool_call_id}
                    className="rounded border border-border bg-background p-2 text-xs"
                  >
                    <div className="flex items-center justify-between">
                      <Badge variant={te.success ? "secondary" : "destructive"}>
                        {te.tool_name}
                      </Badge>
                      <span className="text-muted-foreground">
                        {te.elapsed_ms}ms
                      </span>
                    </div>
                    {te.counts ? (
                      <div className="mt-1 text-muted-foreground">
                        {Object.entries(te.counts)
                          .map(([k, v]) => `${k} = ${v}`)
                          .join(", ")}
                      </div>
                    ) : null}
                    {te.error ? (
                      <div className="mt-1 text-destructive">{te.error}</div>
                    ) : null}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {citations.length > 0 ? (
            <>
              <Separator />
              <section>
                <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Citations ({citations.length})
                </h3>
                <ul className="space-y-2">
                  {citations.map((c, idx) =>
                    c.url ? (
                      // Web-research citation (ADR 0032 A5): a clickable
                      // source link + its docs-first tier badge.
                      <li
                        key={`${c.tool}-${idx}`}
                        className="rounded border border-border bg-background p-2 text-xs"
                      >
                        <div className="flex items-center gap-2">
                          <Globe className="h-3.5 w-3.5 shrink-0 opacity-60" />
                          <span className="font-medium">{c.tool}</span>
                          {c.source ? (
                            <Badge
                              variant={
                                isOfficialSource(c.source)
                                  ? "default"
                                  : "outline"
                              }
                              className="ml-auto shrink-0"
                            >
                              {SOURCE_LABEL[c.source] ?? c.source}
                            </Badge>
                          ) : null}
                        </div>
                        <a
                          href={c.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="mt-1.5 block break-words text-[11px] leading-snug text-primary underline-offset-2 hover:underline"
                          title={c.url}
                        >
                          {c.title?.trim() ? c.title : c.url}
                        </a>
                        {c.title?.trim() ? (
                          <div className="mt-0.5 break-all text-[10px] text-muted-foreground">
                            {c.url}
                          </div>
                        ) : null}
                      </li>
                    ) : (
                      <li
                        key={`${c.tool}-${idx}`}
                        className="rounded border border-border bg-background p-2 text-xs"
                      >
                        <div className="flex items-center gap-2">
                          <FileText className="h-3.5 w-3.5 opacity-60" />
                          <span className="font-medium">{c.tool}</span>
                          {c.result_count !== null ? (
                            <Badge variant="outline" className="ml-auto">
                              {c.result_count}
                            </Badge>
                          ) : null}
                        </div>
                        <pre className="mt-1.5 whitespace-pre-wrap break-words rounded bg-muted/50 p-1.5 font-mono text-[10px] leading-relaxed text-muted-foreground">
                          {JSON.stringify(c.query, null, 2)}
                        </pre>
                      </li>
                    ),
                  )}
                </ul>
              </section>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
