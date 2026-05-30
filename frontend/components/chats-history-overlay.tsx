"use client";

import { Loader2, MessageSquare, Plus, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { Conversation } from "@/lib/types";

type Props = {
  open: boolean;
  conversations: Conversation[];
  /** Slice 5.0c-h: in-flight stream's target conversation (or null). */
  streamingId?: string | null;
  onClose: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
};

/**
 * Slice 5.0c-j: full-screen "Chats" pane reached from the sidebar's
 * History icon. Differs from the sidebar Search in one important way —
 * the sidebar searches conversation *titles*; this pane searches the
 * full *body* of every user and assistant message. Matches surface as
 * snippets with the matched phrase highlighted in context.
 *
 * Layout follows Claude's Chats page: title left, New-chat right,
 * search row below, conversation list filling the rest. The overlay
 * sits above the main chat UI; clicking a result closes the overlay
 * and routes the parent to that conversation.
 */
export function ChatsHistoryOverlay({
  open,
  conversations,
  streamingId,
  onClose,
  onSelect,
  onNew,
}: Props) {
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  // Focus the search input the moment the overlay opens — primary
  // affordance of this screen is "find a chat by content". Reset query
  // too so each open is a fresh search (previous filter doesn't linger).
  useEffect(() => {
    if (open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setQuery("");
      requestAnimationFrame(() => searchRef.current?.focus());
    }
  }, [open]);

  // Close on Escape (modal convention).
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  const results = useMemo(
    () => searchConversations(query, conversations),
    [query, conversations],
  );

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Chats history"
      className="fixed inset-0 z-50 flex flex-col bg-background animate-in fade-in-0 duration-200"
    >
      {/* Header */}
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="h-8 w-8 p-0"
            onClick={onClose}
            aria-label="Close chats history"
            title="Close (Esc)"
          >
            <X className="h-4 w-4" />
          </Button>
          <h2 className="text-xl font-semibold tracking-tight">Chats</h2>
        </div>
        <Button
          size="sm"
          onClick={() => {
            onNew();
            onClose();
          }}
        >
          <Plus className="mr-1 h-4 w-4" /> New chat
        </Button>
      </div>

      {/* Search row */}
      <div className="shrink-0 border-b border-border px-4 py-3">
        <div className="relative mx-auto max-w-3xl">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            ref={searchRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search across every message…"
            aria-label="Search conversations by message content"
            className="h-10 pl-10 pr-10 text-sm"
          />
          {query ? (
            <button
              type="button"
              onClick={() => setQuery("")}
              aria-label="Clear search"
              title="Clear search"
              className="absolute right-2 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded text-muted-foreground hover:bg-accent/50 hover:text-foreground"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </div>
      </div>

      {/* Results */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-4 py-4">
          {conversations.length === 0 ? (
            <EmptyState>
              <p>You haven&apos;t started any conversations yet.</p>
              <Button
                className="mt-3"
                onClick={() => {
                  onNew();
                  onClose();
                }}
              >
                <Plus className="mr-1 h-4 w-4" /> New chat
              </Button>
            </EmptyState>
          ) : results.length === 0 ? (
            <EmptyState>
              <p>
                No messages match{" "}
                <span className="font-medium text-foreground">
                  &ldquo;{query.trim()}&rdquo;
                </span>
                .
              </p>
              <p className="mt-1 text-xs">
                The sidebar Search filters by title; this pane searches the
                full text of every user and assistant message.
              </p>
            </EmptyState>
          ) : (
            <ul className="space-y-2">
              {results.map((r) => (
                <li key={r.conversation.id}>
                  <button
                    type="button"
                    onClick={() => {
                      onSelect(r.conversation.id);
                      onClose();
                    }}
                    className="group/result block w-full rounded-md border border-border bg-card px-4 py-3 text-left transition-colors hover:border-primary/40 hover:bg-accent/30"
                  >
                    <div className="flex items-center gap-2">
                      {r.conversation.id === streamingId ? (
                        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />
                      ) : (
                        <MessageSquare className="h-4 w-4 shrink-0 text-muted-foreground" />
                      )}
                      <span className="line-clamp-1 text-sm font-medium group-hover/result:text-primary">
                        {r.conversation.title}
                      </span>
                      <span className="ml-auto text-[10px] text-muted-foreground">
                        {r.conversation.exchanges.length}{" "}
                        turn
                        {r.conversation.exchanges.length === 1 ? "" : "s"}
                      </span>
                    </div>
                    {r.snippet ? (
                      <p className="mt-1.5 line-clamp-2 text-xs text-muted-foreground">
                        <span className="mr-1.5 inline-block rounded bg-muted px-1 py-0.5 font-mono text-[10px] uppercase">
                          {r.snippet.from}
                        </span>
                        <HighlightedSnippet
                          text={r.snippet.text}
                          match={r.snippet.match}
                        />
                      </p>
                    ) : null}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="py-16 text-center text-sm text-muted-foreground">
      {children}
    </div>
  );
}

/**
 * Render a snippet with the matching phrase visually highlighted. The
 * match is case-insensitive but the original casing is preserved in
 * the output.
 */
function HighlightedSnippet({ text, match }: { text: string; match: string }) {
  if (!match) return <>{text}</>;
  const lower = text.toLowerCase();
  const at = lower.indexOf(match.toLowerCase());
  if (at < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, at)}
      <mark className="rounded bg-amber-200/60 px-0.5 text-foreground dark:bg-amber-400/30">
        {text.slice(at, at + match.length)}
      </mark>
      {text.slice(at + match.length)}
    </>
  );
}

// ─── Search ─────────────────────────────────────────────────────────────────

type SearchResult = {
  conversation: Conversation;
  /** The matching turn's role + a ~120-char snippet, or null when the
   *  match was on the title only / query was empty. */
  snippet: { from: "you" | "wolf" | "title"; text: string; match: string } | null;
};

const SNIPPET_WINDOW = 120;

function searchConversations(
  rawQuery: string,
  conversations: Conversation[],
): SearchResult[] {
  const q = rawQuery.trim().toLowerCase();
  if (!q) {
    return conversations.map((c) => ({ conversation: c, snippet: null }));
  }
  const results: SearchResult[] = [];
  for (const c of conversations) {
    const titleHit = c.title.toLowerCase().includes(q);
    let snippet: SearchResult["snippet"] = null;
    for (const ex of c.exchanges) {
      const inQuestion = ex.question.toLowerCase().includes(q);
      const inAnswer = ex.answer.toLowerCase().includes(q);
      if (inQuestion) {
        snippet = {
          from: "you",
          text: extractWindow(ex.question, q),
          match: rawQuery.trim(),
        };
        break;
      }
      if (inAnswer) {
        snippet = {
          from: "wolf",
          text: extractWindow(ex.answer, q),
          match: rawQuery.trim(),
        };
        break;
      }
    }
    if (snippet) {
      results.push({ conversation: c, snippet });
    } else if (titleHit) {
      results.push({
        conversation: c,
        snippet: {
          from: "title",
          text: c.title,
          match: rawQuery.trim(),
        },
      });
    }
  }
  return results;
}

/**
 * Pull a ~120-char window of `source` centered on the first
 * occurrence of `query` (case-insensitive). Returns the source
 * verbatim when the source fits in the window. Adds ellipses on
 * either side when trimmed.
 */
function extractWindow(source: string, query: string): string {
  if (source.length <= SNIPPET_WINDOW) return source;
  const at = source.toLowerCase().indexOf(query.toLowerCase());
  if (at < 0) return source.slice(0, SNIPPET_WINDOW) + "…";
  const halfPad = Math.floor((SNIPPET_WINDOW - query.length) / 2);
  const start = Math.max(0, at - halfPad);
  const end = Math.min(source.length, at + query.length + halfPad);
  const prefix = start > 0 ? "…" : "";
  const suffix = end < source.length ? "…" : "";
  return prefix + source.slice(start, end) + suffix;
}

