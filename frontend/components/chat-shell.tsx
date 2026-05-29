"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ChatComposer } from "@/components/chat-composer";
import { ChatHeader } from "@/components/chat-header";
import { ChatSidebar } from "@/components/chat-sidebar";
import { CitationsPanel } from "@/components/citations-panel";
import { MessageThread } from "@/components/message-thread";
import { useChatStream } from "@/hooks/use-chat-stream";
import type {
  ChatExchange,
  Citation,
  Conversation,
  ConversationTurn,
  ToolEvent,
} from "@/lib/types";
import { randomId } from "@/lib/uuid";

const EVIDENCE_MIN_WIDTH = 280;
const EVIDENCE_MAX_WIDTH = 720;
const EVIDENCE_DEFAULT_WIDTH = 320;
const STORAGE_SIDEBAR_COLLAPSED = "wolf.sidebar.collapsed";
const STORAGE_EVIDENCE_WIDTH = "wolf.evidence.width";

/**
 * Custom hook: persist a piece of state in localStorage with SSR-safe init.
 * Reads the stored value on mount (so server-render returns the default),
 * then writes back on every change.
 */
function usePersistedState<T>(key: string, initial: T): [T, (next: T) => void] {
  const [value, setValue] = useState<T>(initial);
  // Read from storage once on mount. The SSR pass returns `initial`; on the
  // client we replace it with the persisted value. Setting state from an
  // effect is the right shape for SSR-safe persistence here.
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(key);
      if (raw !== null) {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setValue(JSON.parse(raw) as T);
      }
    } catch {
      /* corrupted entry — ignore and keep default */
    }
  }, [key]);
  // Write on every change.
  useEffect(() => {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* quota / private mode — best effort */
    }
  }, [key, value]);
  return [value, setValue];
}

/**
 * The chat UI:
 *  ┌────────────────────────────────────────────────────────────┐
 *  │ Header: app + tenant switcher + user-avatar dropdown        │
 *  ├──────────┬──────────────────────────────────────────────────┤
 *  │ Sidebar  │  Message thread (scrolls)                        │
 *  │ (collapsible) │                                              │
 *  │          ├──────────────────────────────────────────────────┤
 *  │          │  Composer (fixed at bottom of main column)       │
 *  └──────────┴──────────────────────────────────────────────────┘
 *                                                  ▲
 *                                       Evidence panel (resizable)
 *
 * Multi-turn:
 *   - A `Conversation` is a list of exchanges sharing context.
 *   - Submitting appends to the active conversation; prior turns are sent
 *     as `history` so the agent has context.
 *   - "New" starts a fresh conversation; clicking one in the sidebar
 *     resumes it.
 *
 * Layout details (Slice 5.0c-b):
 *   - Sidebar collapses to an icon rail (persisted to localStorage).
 *   - Evidence panel width is drag-resizable on its left edge
 *     (persisted to localStorage).
 *   - Evidence panel PERSISTS the previous exchange's citations while a
 *     new run is starting up — no flash of empty state.
 */
export function ChatShell() {
  const stream = useChatStream();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvoId, setActiveConvoId] = useState<string | null>(null);
  const archivedRef = useRef<string | null>(null);

  // Layout persistence ---------------------------------------------------
  const [sidebarCollapsed, setSidebarCollapsed] = usePersistedState<boolean>(
    STORAGE_SIDEBAR_COLLAPSED,
    false,
  );
  const [evidenceWidth, setEvidenceWidth] = usePersistedState<number>(
    STORAGE_EVIDENCE_WIDTH,
    EVIDENCE_DEFAULT_WIDTH,
  );

  // When a stream completes, archive the exchange into the active
  // conversation (or start a new one if none was active).
  useEffect(() => {
    if (!stream.exchange) return;
    if (archivedRef.current === stream.exchange.id) return;
    archivedRef.current = stream.exchange.id;

    const ex = stream.exchange;
    setConversations((prev) => {
      if (activeConvoId) {
        return prev.map((c) =>
          c.id === activeConvoId
            ? {
                ...c,
                exchanges: [...c.exchanges, ex],
                updated_at: ex.completed_at,
              }
            : c,
        );
      }
      // Start a fresh conversation seeded by this exchange.
      const newConvo: Conversation = {
        id: randomId(),
        title: titleFromQuestion(ex.question),
        exchanges: [ex],
        created_at: ex.started_at,
        updated_at: ex.completed_at,
      };
      setActiveConvoId(newConvo.id);
      return [newConvo, ...prev];
    });
  }, [stream.exchange, activeConvoId]);

  const activeConversation =
    conversations.find((c) => c.id === activeConvoId) ?? null;

  const handleSubmit = useCallback(
    async (question: string) => {
      const history: ConversationTurn[] = activeConversation
        ? activeConversation.exchanges.flatMap((ex) => [
            { role: "user" as const, content: ex.question },
            { role: "assistant" as const, content: ex.answer },
          ])
        : [];
      await stream.submit(question, history);
    },
    [activeConversation, stream],
  );

  const handleNew = useCallback(() => {
    stream.reset();
    setActiveConvoId(null);
    archivedRef.current = null;
  }, [stream]);

  const handleSelect = useCallback(
    (id: string) => {
      stream.reset();
      setActiveConvoId(id);
      archivedRef.current = null;
    },
    [stream],
  );

  // What to render in the message thread:
  //   - the active conversation's exchanges
  //   - plus the in-flight exchange (or "running" view) if any
  const isRunning = stream.status.phase === "running";
  const visibleExchanges: ChatExchange[] =
    activeConversation?.exchanges ?? [];

  // Evidence: prefer in-flight stream citations, fall back to the latest
  // archived exchange's evidence. This keeps the panel populated through
  // the run-up of a new request instead of flashing empty (Slice 5.0c-b).
  const latestArchived: ChatExchange | null =
    visibleExchanges.length > 0
      ? visibleExchanges[visibleExchanges.length - 1]
      : null;
  const citations: Citation[] =
    isRunning && stream.citations.length > 0
      ? stream.citations
      : (latestArchived?.citations ?? []);
  const toolEvents: ToolEvent[] =
    isRunning && stream.toolEvents.length > 0
      ? stream.toolEvents
      : (latestArchived?.tool_events ?? []);

  // Resizable evidence panel: drag-from-left-edge handle. ---------------
  const resizeStateRef = useRef<{ startX: number; startWidth: number } | null>(
    null,
  );
  const onResizeStart = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.currentTarget.setPointerCapture(e.pointerId);
      resizeStateRef.current = { startX: e.clientX, startWidth: evidenceWidth };
    },
    [evidenceWidth],
  );
  const onResizeMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const state = resizeStateRef.current;
      if (!state) return;
      // Dragging the handle LEFT widens the panel (handle sits on its left edge).
      const delta = state.startX - e.clientX;
      const next = Math.min(
        EVIDENCE_MAX_WIDTH,
        Math.max(EVIDENCE_MIN_WIDTH, state.startWidth + delta),
      );
      setEvidenceWidth(next);
    },
    [setEvidenceWidth],
  );
  const onResizeEnd = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (resizeStateRef.current && e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId);
      }
      resizeStateRef.current = null;
    },
    [],
  );

  return (
    <div className="flex h-screen flex-col">
      <ChatHeader />
      <div className="flex flex-1 overflow-hidden">
        <ChatSidebar
          conversations={conversations}
          activeId={activeConvoId}
          onSelect={handleSelect}
          onNew={handleNew}
          collapsed={sidebarCollapsed}
          onToggleCollapsed={() => setSidebarCollapsed(!sidebarCollapsed)}
        />
        <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          <MessageThread
            exchanges={visibleExchanges}
            stream={stream}
          />
          <div className="shrink-0 border-t border-border bg-card/50 px-4 pt-3 pb-2">
            <ChatComposer
              onSubmit={handleSubmit}
              disabled={isRunning}
            />
            <p className="mt-2 text-center text-[10px] text-muted-foreground">
              Wolf is an AI agent and can make mistakes. Verify critical
              findings against your Wazuh dashboard.
            </p>
          </div>
        </main>
        <aside
          className="hidden border-l border-border bg-card/30 lg:flex"
          style={{ width: `${evidenceWidth}px` }}
        >
          {/* Drag handle: 6px wide hit area on the panel's left edge */}
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize evidence panel"
            className="group relative -ml-1 h-full w-1.5 shrink-0 cursor-col-resize select-none"
            onPointerDown={onResizeStart}
            onPointerMove={onResizeMove}
            onPointerUp={onResizeEnd}
            onPointerCancel={onResizeEnd}
          >
            {/* Visible thin guide that brightens on hover/drag */}
            <span
              aria-hidden="true"
              className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border transition-colors group-hover:bg-primary/40"
            />
          </div>
          <div className="min-w-0 flex-1">
            <CitationsPanel citations={citations} toolEvents={toolEvents} />
          </div>
        </aside>
      </div>
    </div>
  );
}

function titleFromQuestion(question: string): string {
  const trimmed = question.trim().replace(/\s+/g, " ");
  return trimmed.length <= 60 ? trimmed : `${trimmed.slice(0, 57)}…`;
}
