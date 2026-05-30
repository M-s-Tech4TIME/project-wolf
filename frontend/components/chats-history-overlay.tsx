"use client";

import {
  Check,
  Loader2,
  MessageSquare,
  MoreHorizontal,
  Pencil,
  Plus,
  Search,
  Star,
  Trash2,
  X,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { Conversation } from "@/lib/types";

type Props = {
  open: boolean;
  conversations: Conversation[];
  /** Slice 5.0c-h: in-flight stream's target conversation (or null). */
  streamingId?: string | null;
  onClose: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  /**
   * Slice 5.0c-i.2 — per-row actions reached from the result row's
   * "…" menu. Wiring for the menu itself lands in commit 3 of this
   * slice; these props are typed here so the parent can pass them now
   * without a TS error during the gradual roll-out.
   */
  onRename?: (id: string, nextTitle: string) => void;
  onToggleStar?: (id: string) => void;
  /** Bulk delete used by Select-chats mode (commit 3). */
  onBulkDelete?: (ids: string[]) => void;
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
  onRename,
  onToggleStar,
  onBulkDelete,
}: Props) {
  const [query, setQuery] = useState("");
  // Slice 5.0c-i.2 commit 3: selection-mode state. selectionMode is the
  // toggle; selectedIds is the set of conversation ids currently
  // ticked. Both reset to defaults on every open of the overlay so the
  // user never lands inside selection mode unexpectedly.
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  // Focus the search input the moment the overlay opens — primary
  // affordance of this screen is "find a chat by content". Reset query,
  // selection mode, and rename state so each open is a fresh session.
  useEffect(() => {
    if (open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setQuery("");
      setSelectionMode(false);
      setSelectedIds(new Set());
      setRenamingId(null);
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

  // Selection helpers --------------------------------------------------
  const toggleSelection = (id: string) =>
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const enterSelectionWith = (id: string) => {
    setSelectionMode(true);
    setSelectedIds(new Set([id]));
  };
  const selectAllVisible = () =>
    setSelectedIds(new Set(results.map((r) => r.conversation.id)));
  const cancelSelection = () => {
    setSelectionMode(false);
    setSelectedIds(new Set());
  };
  // We refuse to delete a conversation that's currently streaming —
  // the archive effect would write into a removed slot. Mirror that
  // protection here so the Delete button stays disabled when the
  // selection contains the streaming conversation.
  const selectionContainsStreaming =
    streamingId !== null &&
    streamingId !== undefined &&
    selectedIds.has(streamingId);
  const handleDeleteSelected = () => {
    if (selectedIds.size === 0) return;
    onBulkDelete?.(Array.from(selectedIds));
    cancelSelection();
  };

  // Rename helpers -----------------------------------------------------
  const commitRename = (id: string, next: string) => {
    onRename?.(id, next);
    setRenamingId(null);
  };

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Chats history"
      className="fixed inset-0 z-50 flex flex-col bg-background animate-in fade-in-0 duration-200"
    >
      {/* Header — two variants. Normal: title left, "Select chats" +
          "New chat" right. Selection: title left, counter + Select all
          + Delete + Cancel right (image 4). */}
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
        {selectionMode ? (
          <div className="flex items-center gap-2">
            <span className="mr-1 text-xs text-muted-foreground">
              {selectedIds.size} selected
            </span>
            <Button
              size="sm"
              variant="outline"
              onClick={selectAllVisible}
              disabled={results.length === 0}
            >
              Select all
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={handleDeleteSelected}
              disabled={
                selectedIds.size === 0 || selectionContainsStreaming
              }
              title={
                selectionContainsStreaming
                  ? "Can't delete a conversation that's still generating"
                  : undefined
              }
            >
              <Trash2 className="mr-1 h-4 w-4" />
              Delete
            </Button>
            <Button size="sm" variant="ghost" onClick={cancelSelection}>
              Cancel
            </Button>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setSelectionMode(true)}
              disabled={conversations.length === 0}
            >
              Select chats
            </Button>
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
        )}
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
                <ResultRow
                  key={r.conversation.id}
                  result={r}
                  selectionMode={selectionMode}
                  isSelected={selectedIds.has(r.conversation.id)}
                  isStreaming={r.conversation.id === streamingId}
                  isRenaming={r.conversation.id === renamingId}
                  onActivate={() => {
                    if (selectionMode) {
                      toggleSelection(r.conversation.id);
                    } else {
                      onSelect(r.conversation.id);
                      onClose();
                    }
                  }}
                  onEnterSelectionWith={() =>
                    enterSelectionWith(r.conversation.id)
                  }
                  onStar={
                    onToggleStar
                      ? () => onToggleStar(r.conversation.id)
                      : undefined
                  }
                  onStartRename={
                    onRename
                      ? () => setRenamingId(r.conversation.id)
                      : undefined
                  }
                  onCommitRename={(next) =>
                    commitRename(r.conversation.id, next)
                  }
                  onCancelRename={() => setRenamingId(null)}
                  onDelete={
                    onBulkDelete
                      ? () => onBulkDelete([r.conversation.id])
                      : undefined
                  }
                />
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── ResultRow ──────────────────────────────────────────────────────────────

type ResultRowProps = {
  result: SearchResult;
  selectionMode: boolean;
  isSelected: boolean;
  isStreaming: boolean;
  isRenaming: boolean;
  onActivate: () => void;
  onEnterSelectionWith: () => void;
  onStar?: () => void;
  onStartRename?: () => void;
  onCommitRename: (next: string) => void;
  onCancelRename: () => void;
  onDelete?: () => void;
};

function ResultRow({
  result,
  selectionMode,
  isSelected,
  isStreaming,
  isRenaming,
  onActivate,
  onEnterSelectionWith,
  onStar,
  onStartRename,
  onCommitRename,
  onCancelRename,
  onDelete,
}: ResultRowProps) {
  const { conversation, snippet } = result;
  const inputRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState(conversation.title);

  useEffect(() => {
    if (isRenaming) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDraft(conversation.title);
      requestAnimationFrame(() => inputRef.current?.select());
    }
  }, [isRenaming, conversation.title]);

  function handleKeyDown(e: ReactKeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      onCommitRename(draft);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onCancelRename();
    }
  }

  // Inline rename mode — replaces the whole row body with an input.
  if (isRenaming) {
    return (
      <li>
        <div className="rounded-md border border-primary/40 bg-card p-3">
          <Input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => onCommitRename(draft)}
            onKeyDown={handleKeyDown}
            maxLength={80}
            aria-label="Rename conversation"
            className="h-8 text-sm"
          />
          <p className="mt-1 text-[10px] text-muted-foreground">
            Enter to save · Esc to cancel
          </p>
        </div>
      </li>
    );
  }

  // Whether to show the trailing "…" menu. Hidden in selection mode
  // (its actions don't apply when checkboxes are doing the work).
  const showMenu =
    !selectionMode &&
    (onStar !== undefined ||
      onStartRename !== undefined ||
      onDelete !== undefined);

  return (
    <li>
      <div
        className={cn(
          "group/result relative flex items-start gap-2 rounded-md border border-border bg-card pl-3 pr-2 py-3 transition-colors hover:border-primary/40 hover:bg-accent/30",
          isSelected && "border-primary/60 bg-accent/40",
        )}
      >
        {selectionMode ? (
          <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center">
            <input
              type="checkbox"
              checked={isSelected}
              onChange={onActivate}
              aria-label={`Select ${conversation.title}`}
              className="h-4 w-4 cursor-pointer accent-primary"
            />
          </div>
        ) : null}

        <button
          type="button"
          onClick={onActivate}
          className="flex-1 min-w-0 text-left"
        >
          <div className="flex items-center gap-2">
            {isStreaming ? (
              <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />
            ) : conversation.starred ? (
              <Star className="h-4 w-4 shrink-0 fill-amber-400 text-amber-500" />
            ) : (
              <MessageSquare className="h-4 w-4 shrink-0 text-muted-foreground" />
            )}
            <span className="line-clamp-1 text-sm font-medium group-hover/result:text-primary">
              {conversation.title}
            </span>
            <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
              {conversation.exchanges.length} turn
              {conversation.exchanges.length === 1 ? "" : "s"}
            </span>
          </div>
          {snippet ? (
            <p className="mt-1.5 line-clamp-2 text-xs text-muted-foreground">
              <span className="mr-1.5 inline-block rounded bg-muted px-1 py-0.5 font-mono text-[10px] uppercase">
                {snippet.from}
              </span>
              <HighlightedSnippet text={snippet.text} match={snippet.match} />
            </p>
          ) : null}
        </button>

        {showMenu ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                aria-label={`Actions for ${conversation.title}`}
                title="More actions"
                onClick={(e) => e.stopPropagation()}
                className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded text-muted-foreground opacity-0 transition-opacity hover:bg-accent/70 hover:text-foreground focus:opacity-100 group-hover/result:opacity-100"
              >
                <MoreHorizontal className="h-4 w-4" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-44">
              {/* Slice 5.0c-i.4: no preventDefault — let the menu close
                  normally on select. See sidebar's matching comment for
                  why (delete-dialog focus race when the menu stays open). */}
              <DropdownMenuItem onSelect={() => onEnterSelectionWith()}>
                <Check className="mr-2 h-3.5 w-3.5" />
                Select
              </DropdownMenuItem>
              {onStar ? (
                <DropdownMenuItem onSelect={() => onStar()}>
                  <Star
                    className={cn(
                      "mr-2 h-3.5 w-3.5",
                      conversation.starred
                        ? "fill-amber-400 text-amber-500"
                        : "",
                    )}
                  />
                  {conversation.starred ? "Unstar" : "Star"}
                </DropdownMenuItem>
              ) : null}
              {onStartRename ? (
                <DropdownMenuItem onSelect={() => onStartRename()}>
                  <Pencil className="mr-2 h-3.5 w-3.5" />
                  Rename
                </DropdownMenuItem>
              ) : null}
              {onDelete ? (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    variant="destructive"
                    disabled={isStreaming}
                    onSelect={() => {
                      if (isStreaming) return;
                      onDelete();
                    }}
                  >
                    <Trash2 className="mr-2 h-3.5 w-3.5" />
                    Delete
                  </DropdownMenuItem>
                </>
              ) : null}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </div>
    </li>
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

