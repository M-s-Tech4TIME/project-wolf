"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ChatComposer } from "@/components/chat-composer";
import { ChatHeader } from "@/components/chat-header";
import { ChatSidebar } from "@/components/chat-sidebar";
import { ChatsHistoryOverlay } from "@/components/chats-history-overlay";
import { CitationsPanel } from "@/components/citations-panel";
import { ConfirmDialog } from "@/components/confirm-dialog";
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
  // Slice 5.0c-h: which conversation the current in-flight stream belongs
  // to. May differ from `activeConvoId` if the user navigated away during
  // the run. Null when idle. The archive effect uses this — never
  // `activeConvoId` — to find the conversation to append the completed
  // exchange to.
  const [streamingConvoId, setStreamingConvoId] = useState<string | null>(null);
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

  // When a stream completes, archive the exchange into the conversation
  // it belongs to (Slice 5.0c-h — `streamingConvoId`, not `activeConvoId`,
  // because the user may have switched conversations during the run).
  // The conversation slot itself was created synchronously inside
  // `handleSubmit` so it has already been visible in the sidebar for the
  // whole stream — this effect just appends the finished exchange to it.
  useEffect(() => {
    if (!stream.exchange || !streamingConvoId) return;
    if (archivedRef.current === stream.exchange.id) return;
    archivedRef.current = stream.exchange.id;

    const ex = stream.exchange;
    const targetId = streamingConvoId;
    setConversations((prev) =>
      prev.map((c) =>
        c.id === targetId
          ? {
              ...c,
              exchanges: [...c.exchanges, ex],
              updated_at: ex.completed_at,
            }
          : c,
      ),
    );
    setStreamingConvoId(null);
  }, [stream.exchange, streamingConvoId]);

  const activeConversation =
    conversations.find((c) => c.id === activeConvoId) ?? null;

  const handleSubmit = useCallback(
    async (question: string) => {
      // One in-flight stream at a time. The composer is disabled while
      // `isRunning`, so this guard is a belt-and-braces against a race
      // (e.g. quick Enter-twice when the previous run only just flipped
      // `phase` to "done" but a new submit hasn't entered "running" yet).
      if (stream.status.phase === "running") return;

      let targetConvoId = activeConvoId;
      let history: ConversationTurn[] = [];
      if (targetConvoId) {
        const c = conversations.find((c) => c.id === targetConvoId);
        history = c
          ? c.exchanges.flatMap((ex) => [
              { role: "user" as const, content: ex.question },
              { role: "assistant" as const, content: ex.answer },
            ])
          : [];
      } else {
        // Slice 5.0c-h: create the conversation slot synchronously so it
        // appears in the sidebar the moment the user submits — not after
        // the stream completes. The empty exchanges array means the
        // sidebar item shows "0 turns · 0 tool calls" until the live
        // exchange archives in.
        const now = new Date().toISOString();
        const newConvo: Conversation = {
          id: randomId(),
          title: titleFromQuestion(question),
          exchanges: [],
          created_at: now,
          updated_at: now,
        };
        setConversations((prev) => [newConvo, ...prev]);
        targetConvoId = newConvo.id;
        setActiveConvoId(targetConvoId);
      }
      setStreamingConvoId(targetConvoId);
      await stream.submit(question, history);
    },
    [activeConvoId, conversations, stream],
  );

  const handleNew = useCallback(() => {
    // If a stream is in-flight, leave it alone — just take the user to
    // an empty greeting. The previous conversation keeps streaming in
    // the background; the user can return to it via the sidebar.
    if (stream.status.phase !== "running") {
      stream.reset();
      archivedRef.current = null;
    }
    setActiveConvoId(null);
  }, [stream]);

  const handleSelect = useCallback(
    (id: string) => {
      // Same survival rule: switching conversations while a stream is
      // running must not kill the stream. Only reset the stream state
      // when it's already settled.
      if (stream.status.phase !== "running") {
        stream.reset();
        archivedRef.current = null;
      }
      setActiveConvoId(id);
    },
    [stream],
  );

  // Slice 5.0c-i: conversation rename. Both the top-bar title and the
  // sidebar's per-item "…" menu route here. Trims whitespace, rejects
  // an empty rename (the convo keeps its old title), and caps the
  // length so the sidebar item can still render on one line.
  const handleRename = useCallback((id: string, nextTitle: string) => {
    const trimmed = nextTitle.trim();
    if (!trimmed) return;
    setConversations((prev) =>
      prev.map((c) =>
        c.id === id ? { ...c, title: trimmed.slice(0, 80) } : c,
      ),
    );
  }, []);

  // Slice 5.0c-i.2: star / unstar. Stars surface the conversation in
  // its own "Starred" section above "Recents" in the sidebar; toggling
  // does not change updated_at, so position within the section is
  // preserved.
  const handleToggleStar = useCallback((id: string) => {
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, starred: !c.starred } : c)),
    );
  }, []);

  // Slice 5.0c-i.3: pending-delete state for the app-native
  // confirmation dialog. handleDelete / handleBulkDelete *queue* the
  // deletion here; the dialog renders, and confirmPendingDelete
  // executes only when the user accepts. The streaming-protection
  // path now silently no-ops (the menu items are also disabled for
  // streaming conversations, so this path shouldn't fire — but the
  // belt-and-braces stays).
  const [pendingDelete, setPendingDelete] = useState<{
    ids: string[];
    titles: string[];
  } | null>(null);

  const handleDelete = useCallback(
    (id: string) => {
      if (streamingConvoId === id) return;
      const target = conversations.find((c) => c.id === id);
      if (!target) return;
      setPendingDelete({ ids: [id], titles: [target.title] });
    },
    [streamingConvoId, conversations],
  );

  const handleBulkDelete = useCallback(
    (ids: string[]) => {
      const safeIds = ids.filter((id) => id !== streamingConvoId);
      if (safeIds.length === 0) return;
      const titles = safeIds
        .map((id) => conversations.find((c) => c.id === id)?.title)
        .filter((t): t is string => typeof t === "string");
      setPendingDelete({ ids: safeIds, titles });
    },
    [streamingConvoId, conversations],
  );

  const confirmPendingDelete = useCallback(() => {
    if (!pendingDelete) return;
    const ids = pendingDelete.ids;
    setConversations((prev) => prev.filter((c) => !ids.includes(c.id)));
    if (activeConvoId && ids.includes(activeConvoId)) {
      setActiveConvoId(null);
      if (stream.status.phase !== "running") {
        stream.reset();
        archivedRef.current = null;
      }
    }
    setPendingDelete(null);
  }, [pendingDelete, activeConvoId, stream]);

  const cancelPendingDelete = useCallback(() => {
    setPendingDelete(null);
  }, []);

  // Slice 5.0c-i.2: keep the sidebar / overlay sorted by most-recent
  // activity. updated_at is set on every archive, so a new exchange
  // bubbles its conversation back to the top of its section.
  const sortedConversations = useMemo(
    () =>
      [...conversations].sort((a, b) =>
        (b.updated_at || "").localeCompare(a.updated_at || ""),
      ),
    [conversations],
  );

  // Slice 5.0c-j: full-screen chats-history pane open state.
  const [chatsOverlayOpen, setChatsOverlayOpen] = useState(false);

  // Composer draft handoff (Slice 5.0c-f). The hover Edit / Retry actions
  // and the new-chat greeting screen all want the same thing: prefill the
  // composer with some text and focus it. We hold the draft here and pass
  // it down; the nonce bump re-triggers the child effect even for
  // identical text (a second Retry click on the same question still
  // refocuses + reselects).
  const [composerDraft, setComposerDraft] = useState<{
    value: string;
    nonce: number;
  }>({ value: "", nonce: 0 });
  const setDraft = useCallback((value: string) => {
    setComposerDraft((prev) => ({ value, nonce: prev.nonce + 1 }));
  }, []);

  // Retry-on-Wolf-response (Slice 5.0c-g). Submits the originating
  // question with retry_nudge=true and history that includes the
  // previous Q→A pair, so the model can critique its prior attempt.
  // Unlike the composer-side Retry chip (which only prefills), this
  // immediately fires a new request.
  const handleAssistantRetry = useCallback(
    async (originatingQuestion: string) => {
      if (!activeConversation) return;
      if (stream.status.phase === "running") return;
      const history: ConversationTurn[] = activeConversation.exchanges.flatMap(
        (ex) => [
          { role: "user" as const, content: ex.question },
          { role: "assistant" as const, content: ex.answer },
        ],
      );
      setStreamingConvoId(activeConversation.id);
      await stream.submit(originatingQuestion, history, { retryNudge: true });
    },
    [activeConversation, stream],
  );

  // What to render in the message thread:
  //   - the active conversation's exchanges
  //   - plus the in-flight exchange (or "running" view) if any — but
  //     ONLY when the active conversation is the one being streamed
  //     (Slice 5.0c-h). Otherwise the user navigated away; the live view
  //     belongs to whichever conversation `streamingConvoId` points to.
  const isRunning = stream.status.phase === "running";
  const isActiveStreaming =
    isRunning && activeConvoId !== null && activeConvoId === streamingConvoId;
  const visibleExchanges: ChatExchange[] =
    activeConversation?.exchanges ?? [];

  // Evidence: prefer in-flight stream citations only when the user is
  // looking at the streaming conversation. Otherwise fall back to the
  // latest archived exchange's evidence (or empty when the active convo
  // hasn't accumulated turns yet).
  const latestArchived: ChatExchange | null =
    visibleExchanges.length > 0
      ? visibleExchanges[visibleExchanges.length - 1]
      : null;
  const citations: Citation[] =
    isActiveStreaming && stream.citations.length > 0
      ? stream.citations
      : (latestArchived?.citations ?? []);
  const toolEvents: ToolEvent[] =
    isActiveStreaming && stream.toolEvents.length > 0
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
      <ChatHeader
        title={activeConversation?.title ?? null}
        onRename={
          activeConversation
            ? (next) => handleRename(activeConversation.id, next)
            : undefined
        }
      />
      <div className="flex flex-1 overflow-hidden">
        <ChatSidebar
          conversations={sortedConversations}
          activeId={activeConvoId}
          streamingId={streamingConvoId}
          onSelect={handleSelect}
          onNew={handleNew}
          onRename={handleRename}
          onToggleStar={handleToggleStar}
          onDelete={handleDelete}
          onOpenChatsHistory={() => setChatsOverlayOpen(true)}
          collapsed={sidebarCollapsed}
          onToggleCollapsed={() => setSidebarCollapsed(!sidebarCollapsed)}
        />
        <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          <MessageThread
            exchanges={visibleExchanges}
            stream={stream}
            isActiveStreaming={isActiveStreaming}
            onEdit={setDraft}
            onRetry={setDraft}
            onQuickAsk={setDraft}
            onAssistantRetry={handleAssistantRetry}
          />
          <div className="shrink-0 border-t border-border bg-card/50 px-4 pt-3 pb-2">
            {/* Slice 5.0c-h: cross-conversation in-flight notice. When
                the stream belongs to a different conversation than the
                one currently displayed, surface that fact so the
                disabled composer isn't unexplained. */}
            {isRunning && !isActiveStreaming ? (
              <p className="mb-2 text-center text-[10px] text-muted-foreground">
                Another conversation is generating an answer. Wait for it
                to finish, or open it from the sidebar to follow along.
              </p>
            ) : null}
            <ChatComposer
              onSubmit={handleSubmit}
              disabled={isRunning}
              draft={composerDraft.nonce > 0 ? composerDraft : undefined}
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
      {/* Slice 5.0c-j: full-screen chats history pane. Mounted at the
          shell level so it covers the entire viewport (header included)
          when open. Closed state renders nothing. */}
      <ChatsHistoryOverlay
        open={chatsOverlayOpen}
        conversations={sortedConversations}
        streamingId={streamingConvoId}
        onClose={() => setChatsOverlayOpen(false)}
        onSelect={handleSelect}
        onNew={handleNew}
        onRename={handleRename}
        onToggleStar={handleToggleStar}
        onBulkDelete={handleBulkDelete}
      />
      {/* Slice 5.0c-i.3: app-native confirmation dialog. Mounted at the
          shell level so it sits above both the chat UI and the chats-
          history overlay; the dialog's own z-index (60) puts it above
          the overlay's z-50. */}
      <ConfirmDialog
        open={pendingDelete !== null}
        variant="destructive"
        title={
          pendingDelete && pendingDelete.ids.length > 1
            ? `Delete ${pendingDelete.ids.length} conversations?`
            : "Delete this conversation?"
        }
        description={
          pendingDelete ? (
            pendingDelete.ids.length === 1 ? (
              <>
                <span className="font-medium text-foreground">
                  &ldquo;{pendingDelete.titles[0]}&rdquo;
                </span>{" "}
                will be permanently removed. This can&apos;t be undone.
              </>
            ) : (
              <>
                The selected conversations will be permanently removed.
                This can&apos;t be undone.
              </>
            )
          ) : (
            ""
          )
        }
        confirmLabel={
          pendingDelete && pendingDelete.ids.length > 1
            ? `Delete ${pendingDelete.ids.length}`
            : "Delete"
        }
        onConfirm={confirmPendingDelete}
        onCancel={cancelPendingDelete}
      />
    </div>
  );
}

function titleFromQuestion(question: string): string {
  const trimmed = question.trim().replace(/\s+/g, " ");
  return trimmed.length <= 60 ? trimmed : `${trimmed.slice(0, 57)}…`;
}
