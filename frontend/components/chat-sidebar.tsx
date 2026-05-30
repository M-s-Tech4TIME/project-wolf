"use client";

import {
  Building2,
  ChevronLeft,
  ChevronRight,
  LogOut,
  Mail,
  MessageSquare,
  Plus,
  Search,
  UserCircle,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

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
  onSelect: (id: string) => void;
  onNew: () => void;
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
  onSelect,
  onNew,
  collapsed,
  onToggleCollapsed,
}: Props) {
  const [query, setQuery] = useState("");
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
            title="Search conversations"
          >
            <Search className="h-4 w-4" />
          </Button>
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
            <div className="space-y-1 px-2 pb-3">
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
                filtered.map((c) => {
                  const totalToolCalls = c.exchanges.reduce(
                    (sum, ex) => sum + ex.tool_call_count,
                    0,
                  );
                  const turns = c.exchanges.length;
                  return (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => onSelect(c.id)}
                      className={cn(
                        "flex w-full flex-col items-start gap-1 rounded-md px-3 py-2 text-left transition-colors",
                        c.id === activeId
                          ? "bg-accent text-accent-foreground"
                          : "hover:bg-accent/50",
                      )}
                    >
                      <div className="flex w-full items-center gap-2">
                        <MessageSquare className="h-3.5 w-3.5 shrink-0 opacity-70" />
                        <span className="line-clamp-1 text-sm font-medium">
                          {c.title}
                        </span>
                      </div>
                      <div className="text-[10px] text-muted-foreground">
                        {turns} turn{turns === 1 ? "" : "s"} · {totalToolCalls}{" "}
                        tool call{totalToolCalls === 1 ? "" : "s"}
                      </div>
                    </button>
                  );
                })
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
