"use client";

import {
  ChevronLeft,
  ChevronRight,
  MessageSquare,
  Plus,
} from "lucide-react";

import { Button } from "@/components/ui/button";
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

export function ChatSidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  collapsed,
  onToggleCollapsed,
}: Props) {
  return (
    <aside
      className={cn(
        "hidden flex-col border-r border-border bg-card/40 transition-[width] duration-200 ease-in-out md:flex",
        collapsed ? "w-12" : "w-72",
      )}
    >
      {/* Header row: collapse toggle + (when expanded) label + New */}
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

      {collapsed ? (
        // Collapsed: just a "+ new conversation" rail; clicking a conversation
        // bubble would require labels we can't fit at this width, so we expose
        // only the most common action.
        <div className="flex flex-col items-center gap-1 px-2">
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
        </div>
      ) : (
        <ScrollArea className="flex-1">
          <div className="space-y-1 px-2 pb-3">
            {conversations.length === 0 ? (
              <div className="px-3 py-8 text-center text-xs text-muted-foreground">
                No conversations yet.
                <br />
                Ask something to start.
              </div>
            ) : (
              conversations.map((c) => {
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
      )}
    </aside>
  );
}
