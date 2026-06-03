"use client";

import {
  Building2,
  ChevronLeft,
  ChevronRight,
  History,
  Loader2,
  LogOut,
  Mail,
  MessageSquare,
  MoreHorizontal,
  Pencil,
  Plus,
  Search,
  Star,
  Trash2,
  UserCircle,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type { Conversation } from "@/lib/types";

type Props = {
  conversations: Conversation[];
  activeId: string | null;
  /**
   * Set of conversation IDs that are currently streaming. Slice 5.0c-k:
   * multiple conversations can stream simultaneously, so this is a Set,
   * not a single id. Per-row check is `streamingIds?.has(c.id)`.
   */
  streamingIds?: Set<string>;
  onSelect: (id: string) => void;
  onNew: () => void;
  /** Slice 5.0c-i: per-item rename via the "…" menu (or top-bar title). */
  onRename?: (id: string, nextTitle: string) => void;
  /** Slice 5.0c-i.2: toggle a conversation's starred flag. Starred
   *  conversations float into their own "Starred" section above
   *  "Recents". */
  onToggleStar?: (id: string) => void;
  /** Slice 5.0c-i.2: permanently delete a conversation. The handler
   *  is expected to confirm with the user; this prop is a fire-and-
   *  forget signal. Disabled by the parent (via the omitted-prop
   *  pattern) for conversations currently being streamed. */
  onDelete?: (id: string) => void;
  /** Slice 5.0c-j: opens the full-screen chats-history pane with
   *  content search across every user + assistant message. */
  onOpenChatsHistory?: () => void;
  /** Sidebar collapsed state — owned by parent so the layout can shrink. */
  collapsed: boolean;
  onToggleCollapsed: () => void;
};

function initialsOf(displayName?: string, email?: string): string {
  const trimmed = (displayName ?? "").trim();
  if (trimmed) {
    const parts = trimmed.split(/\s+/);
    const first = parts[0]?.[0] ?? "";
    const second = parts[1]?.[0] ?? parts[0]?.[1] ?? "";
    return (first + second).toUpperCase() || "U";
  }
  const local = (email ?? "").split("@")[0];
  return (local.slice(0, 2) || "U").toUpperCase();
}

export function ChatSidebar({
  conversations,
  activeId,
  streamingIds,
  onSelect,
  onNew,
  onRename,
  onToggleStar,
  onDelete,
  onOpenChatsHistory,
  collapsed,
  onToggleCollapsed,
}: Props) {
  const [query, setQuery] = useState("");
  // Which conversation is currently being inline-renamed in the sidebar
  // (null when no item is in edit mode). Slice 5.0c-i.
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  // `requestFocus` is bumped by the collapsed-mode Search icon: it expands
  // the sidebar and then focuses the input on the next paint. We use a
  // monotonically-increasing tick so re-clicks after focus drift still
  // re-trigger the focus effect.
  const [focusTick, setFocusTick] = useState(0);

  useEffect(() => {
    if (collapsed) return;
    if (focusTick === 0) return;
    // Run on next frame so the layout transition can settle.
    const id = requestAnimationFrame(() => {
      searchInputRef.current?.focus();
    });
    return () => cancelAnimationFrame(id);
  }, [collapsed, focusTick]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return conversations;
    return conversations.filter((c) => c.title.toLowerCase().includes(q));
  }, [conversations, query]);

  // Slice 5.0c-i.2: split into Starred + Recents. Each section keeps
  // the parent's updated_at order — stars don't get re-ranked when
  // toggled, only relocated.
  const starredConvos = useMemo(
    () => filtered.filter((c) => c.starred),
    [filtered],
  );
  const recentConvos = useMemo(
    () => filtered.filter((c) => !c.starred),
    [filtered],
  );

  const handleSearchFromCollapsed = () => {
    if (collapsed) onToggleCollapsed();
    setFocusTick((n) => n + 1);
  };

  return (
    <aside
      className={cn(
        "hidden flex-col border-r border-border bg-card/40 transition-[width] duration-200 ease-in-out md:flex",
        collapsed ? "w-12" : "w-72",
      )}
    >
      {/* Top: collapse toggle + (when expanded) label + New */}
      <div
        className={cn(
          "flex items-center gap-1 px-2 py-3",
          collapsed ? "justify-center" : "justify-between",
        )}
      >
        <Button
          variant="ghost"
          size="sm"
          className="h-8 w-8 p-0"
          onClick={onToggleCollapsed}
          aria-label={collapsed ? "Expand conversations" : "Collapse conversations"}
          title={collapsed ? "Expand conversations" : "Collapse conversations"}
        >
          {collapsed ? (
            <ChevronRight className="h-4 w-4" />
          ) : (
            <ChevronLeft className="h-4 w-4" />
          )}
        </Button>
        {!collapsed && (
          <>
            <span className="ml-1 flex-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Conversations
            </span>
            {onOpenChatsHistory ? (
              <Button
                variant="ghost"
                size="sm"
                className="h-8 w-8 p-0"
                onClick={onOpenChatsHistory}
                aria-label="Browse chats"
                title="Browse chats (full-text search)"
              >
                <History className="h-4 w-4" />
              </Button>
            ) : null}
            <Button variant="ghost" size="sm" onClick={onNew}>
              <Plus className="mr-1 h-4 w-4" /> New
            </Button>
          </>
        )}
      </div>

      {/* Middle: list (or icon rail when collapsed) */}
      {collapsed ? (
        <div className="flex flex-1 flex-col items-center gap-1 px-2">
          <Button
            variant="ghost"
            size="sm"
            className="h-8 w-8 p-0"
            onClick={onNew}
            aria-label="New conversation"
            title="New conversation"
          >
            <Plus className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-8 w-8 p-0"
            onClick={handleSearchFromCollapsed}
            aria-label="Search conversations"
            title="Search conversations (titles)"
          >
            <Search className="h-4 w-4" />
          </Button>
          {onOpenChatsHistory ? (
            <Button
              variant="ghost"
              size="sm"
              className="h-8 w-8 p-0"
              onClick={onOpenChatsHistory}
              aria-label="Browse chats"
              title="Browse chats (full-text search)"
            >
              <History className="h-4 w-4" />
            </Button>
          ) : null}
        </div>
      ) : (
        <>
          {/* Search input (Slice 5.0c-f). Filters the list client-side by
              title — the only metadata we surface today. */}
          <div className="px-2 pb-2">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                ref={searchInputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search conversations…"
                aria-label="Search conversations"
                className="h-8 pl-7 pr-7 text-xs"
              />
              {query ? (
                <button
                  type="button"
                  onClick={() => setQuery("")}
                  aria-label="Clear search"
                  title="Clear search"
                  className="absolute right-1 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                >
                  <X className="h-3 w-3" />
                </button>
              ) : null}
            </div>
          </div>
          <ScrollArea className="flex-1">
            <div className="space-y-3 px-2 pb-3">
              {conversations.length === 0 ? (
                <div className="px-3 py-8 text-center text-xs text-muted-foreground">
                  No conversations yet.
                  <br />
                  Ask something to start.
                </div>
              ) : filtered.length === 0 ? (
                <div className="px-3 py-8 text-center text-xs text-muted-foreground">
                  No matches for{" "}
                  <span className="font-medium text-foreground">
                    “{query.trim()}”
                  </span>
                  .
                </div>
              ) : (
                <>
                  {starredConvos.length > 0 ? (
                    <ConversationListSection
                      label="Starred"
                      conversations={starredConvos}
                      activeId={activeId}
                      streamingIds={streamingIds}
                      renamingId={renamingId}
                      onSelect={onSelect}
                      onStartRename={(id) => setRenamingId(id)}
                      onCommitRename={(id, next) => {
                        setRenamingId(null);
                        onRename?.(id, next);
                      }}
                      onCancelRename={() => setRenamingId(null)}
                      onToggleStar={onToggleStar}
                      onDelete={onDelete}
                      canRename={!!onRename}
                    />
                  ) : null}
                  <ConversationListSection
                    label={starredConvos.length > 0 ? "Recents" : null}
                    conversations={recentConvos}
                    activeId={activeId}
                    streamingIds={streamingIds}
                    renamingId={renamingId}
                    onSelect={onSelect}
                    onStartRename={(id) => setRenamingId(id)}
                    onCommitRename={(id, next) => {
                      setRenamingId(null);
                      onRename?.(id, next);
                    }}
                    onCancelRename={() => setRenamingId(null)}
                    onToggleStar={onToggleStar}
                    onDelete={onDelete}
                    canRename={!!onRename}
                  />
                </>
              )}
            </div>
          </ScrollArea>
        </>
      )}

      {/* Bottom: profile footer — Claude-style */}
      <SidebarProfileFooter collapsed={collapsed} />
    </aside>
  );
}

/**
 * Slice 5.0c-i.2: a labelled group of conversation rows (e.g. "Starred"
 * / "Recents"). The "Recents" label is only shown when there's also a
 * "Starred" section above — when nothing is starred we just render a
 * flat list without a header (avoids a "Recents" label hovering over
 * the only group of items).
 */
function ConversationListSection({
  label,
  conversations,
  activeId,
  streamingIds,
  renamingId,
  onSelect,
  onStartRename,
  onCommitRename,
  onCancelRename,
  onToggleStar,
  onDelete,
  canRename,
}: {
  label: string | null;
  conversations: Conversation[];
  activeId: string | null;
  streamingIds?: Set<string>;
  renamingId: string | null;
  onSelect: (id: string) => void;
  onStartRename: (id: string) => void;
  onCommitRename: (id: string, next: string) => void;
  onCancelRename: () => void;
  onToggleStar?: (id: string) => void;
  onDelete?: (id: string) => void;
  canRename: boolean;
}) {
  if (conversations.length === 0) return null;
  return (
    <div className="space-y-1">
      {label ? (
        <div className="px-3 pt-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {label}
        </div>
      ) : null}
      {conversations.map((c) => (
        <ConversationListItem
          key={c.id}
          conversation={c}
          isActive={c.id === activeId}
          isStreaming={streamingIds?.has(c.id) ?? false}
          isRenaming={c.id === renamingId}
          onSelect={() => onSelect(c.id)}
          onStartRename={() => onStartRename(c.id)}
          onCommitRename={(next) => onCommitRename(c.id, next)}
          onCancelRename={onCancelRename}
          onToggleStar={onToggleStar ? () => onToggleStar(c.id) : undefined}
          onDelete={onDelete ? () => onDelete(c.id) : undefined}
          canRename={canRename}
        />
      ))}
    </div>
  );
}

/**
 * One row in the conversation list. Renders as either:
 *   - a click-to-select button (default)
 *   - an inline rename input (when isRenaming)
 * Plus a hover-revealed "…" menu button that opens a dropdown with the
 * Rename / Star / Delete actions (Slice 5.0c-i.2). The "…" stays out
 * of the way until the row is hovered so the list reads cleanly when
 * the user is just browsing.
 */
function ConversationListItem({
  conversation,
  isActive,
  isStreaming,
  isRenaming,
  onSelect,
  onStartRename,
  onCommitRename,
  onCancelRename,
  onToggleStar,
  onDelete,
  canRename,
}: {
  conversation: Conversation;
  isActive: boolean;
  isStreaming: boolean;
  isRenaming: boolean;
  onSelect: () => void;
  onStartRename: () => void;
  onCommitRename: (next: string) => void;
  onCancelRename: () => void;
  onToggleStar?: () => void;
  onDelete?: () => void;
  canRename: boolean;
}) {
  // Slice 5.0c-l v4 (node-tree refactor): "turns" counts user
  // messages across ALL branches (since the sidebar's summary should
  // reflect the conversation's overall activity, not just the
  // currently-selected branch). Same for tool calls — sum across
  // every assistant node in the tree.
  let totalToolCalls = 0;
  let turns = 0;
  for (const node of Object.values(conversation.nodes)) {
    if (node.role === "user") {
      turns += 1;
    } else {
      totalToolCalls += node.tool_call_count;
    }
  }
  const inputRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState(conversation.title);

  useEffect(() => {
    if (isRenaming) {
      // setState in an effect here: we're syncing the editable draft to
      // the current authoritative title when the row enters edit mode.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDraft(conversation.title);
      // Focus + select on next paint so the rename is a single keystroke
      // replace.
      requestAnimationFrame(() => inputRef.current?.select());
    }
  }, [isRenaming, conversation.title]);

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      onCommitRename(draft);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onCancelRename();
    }
  }

  if (isRenaming) {
    return (
      <div
        className={cn(
          "rounded-md px-3 py-2",
          isActive ? "bg-accent text-accent-foreground" : "bg-accent/30",
        )}
      >
        <Input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => onCommitRename(draft)}
          onKeyDown={handleKeyDown}
          maxLength={80}
          aria-label="Rename conversation"
          className="h-7 text-sm"
        />
        <p className="mt-1 text-[10px] text-muted-foreground">
          Enter to save · Esc to cancel
        </p>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "group/item relative rounded-md transition-colors",
        isActive
          ? "bg-accent text-accent-foreground"
          : "hover:bg-accent/50",
      )}
    >
      <button
        type="button"
        onClick={onSelect}
        className="flex w-full flex-col items-start gap-1 px-3 py-2 pr-9 text-left"
      >
        <div className="flex w-full items-center gap-2">
          {isStreaming ? (
            <Loader2
              className="h-3.5 w-3.5 shrink-0 animate-spin text-primary"
              aria-label="Generating an answer"
            />
          ) : conversation.starred ? (
            <Star
              className="h-3.5 w-3.5 shrink-0 fill-amber-400 text-amber-500"
              aria-label="Starred"
            />
          ) : (
            <MessageSquare className="h-3.5 w-3.5 shrink-0 opacity-70" />
          )}
          <span className="line-clamp-1 text-sm font-medium">
            {conversation.title}
          </span>
        </div>
        <div className="text-[10px] text-muted-foreground">
          {isStreaming
            ? "Generating…"
            : `${turns} turn${turns === 1 ? "" : "s"} · ${totalToolCalls} tool call${totalToolCalls === 1 ? "" : "s"}`}
        </div>
      </button>
      {canRename || onToggleStar || onDelete ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              aria-label={`Actions for ${conversation.title}`}
              title="More actions"
              onClick={(e) => e.stopPropagation()}
              className="absolute right-1 top-1/2 -translate-y-1/2 flex h-7 w-7 items-center justify-center rounded text-muted-foreground opacity-0 transition-opacity hover:bg-accent/70 hover:text-foreground focus:opacity-100 group-hover/item:opacity-100"
            >
              <MoreHorizontal className="h-4 w-4" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-44">
            {/* Slice 5.0c-i.4: previously these onSelect handlers called
                e.preventDefault(), which in Radix DropdownMenu keeps the
                menu OPEN after selection. With the menu still open, the
                outside-click handler intercepted the first click in any
                follow-up UI (notably the Delete confirm dialog), causing
                the "needs an extra click" focus bug the user reported.
                Letting the menu close normally fixes it. */}
            {onToggleStar ? (
              <DropdownMenuItem onSelect={() => onToggleStar()}>
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
            {canRename ? (
              <DropdownMenuItem onSelect={() => onStartRename()}>
                <Pencil className="mr-2 h-3.5 w-3.5" />
                Rename
              </DropdownMenuItem>
            ) : null}
            {onDelete ? (
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
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}
    </div>
  );
}

/**
 * Bottom-pinned profile row. Avatar always; when expanded, also displays
 * the user's display name and role. Clicking it opens a dropdown with
 * email, current tenant, user_id prefix, and sign-out.
 */
function SidebarProfileFooter({ collapsed }: { collapsed: boolean }) {
  const { me, tenants, signOut } = useAuth();
  const initials = initialsOf(me?.display_name, me?.email);
  const currentTenant = tenants.find((t) => t.id === me?.tenant_id);
  const displayName = me?.display_name?.trim() || me?.email || "Signed in";

  return (
    <div className="border-t border-border bg-card/60 p-2">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className={cn(
              "flex w-full items-center gap-2 rounded-md p-1.5 text-left transition-colors hover:bg-accent/50",
              collapsed && "justify-center",
            )}
            aria-label="Account menu"
            title={collapsed ? displayName : undefined}
          >
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/15 text-xs font-semibold text-primary">
              {initials}
            </span>
            {!collapsed && (
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate text-sm font-medium">
                  {displayName}
                </span>
                <span className="truncate text-[10px] text-muted-foreground">
                  {me?.role ?? "—"}
                  {currentTenant
                    ? ` · ${currentTenant.name ?? currentTenant.slug}`
                    : ""}
                </span>
              </span>
            )}
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align={collapsed ? "start" : "end"}
          side="top"
          className="w-64"
        >
          <DropdownMenuLabel className="flex items-center gap-2 py-2">
            <span className="flex h-9 w-9 items-center justify-center rounded-full bg-primary/15 text-xs font-semibold text-primary">
              {initials}
            </span>
            <span className="flex flex-col">
              <span className="text-sm">{displayName}</span>
              <span className="text-[10px] font-normal text-muted-foreground">
                Role: {me?.role ?? "—"}
              </span>
            </span>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          {/* Read-only identity rows — `disabled` on shadcn's DropdownMenuItem
              fades the text too far; render as plain rows that still respect
              the menu's keyboard nav but keep contrast readable. */}
          <div className="px-2 py-1 text-xs text-foreground">
            <div className="flex items-center gap-2 py-1">
              <Mail className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="truncate">{me?.email ?? "—"}</span>
            </div>
            <div className="flex items-center gap-2 py-1">
              <Building2 className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="truncate">
                {currentTenant?.name ?? currentTenant?.slug ?? "—"}
              </span>
            </div>
            <div className="flex items-center gap-2 py-1">
              <UserCircle className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="truncate font-mono">
                {me?.user_id?.slice(0, 8) ?? "—"}
              </span>
            </div>
          </div>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => void signOut()}>
            <LogOut className="mr-2 h-4 w-4" />
            Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
