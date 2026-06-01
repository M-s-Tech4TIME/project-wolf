"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ChatComposer } from "@/components/chat-composer";
import { ChatHeader } from "@/components/chat-header";
import { ChatSidebar } from "@/components/chat-sidebar";
import { ChatsHistoryOverlay } from "@/components/chats-history-overlay";
import { CitationsPanel } from "@/components/citations-panel";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { MessageThread } from "@/components/message-thread";
import {
  NEUTRAL_STREAM,
  useConversationStreams,
} from "@/hooks/use-conversation-streams";
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
 * Slice 5.0c-k: concurrent streams. The stream manager
 * (`useConversationStreams`) keeps independent state per conversation,
 * so the user can start a second conversation while a first is still
 * generating. The composer textarea stays editable even while THIS
 * conversation is streaming (drafts survive); only the Send button is
 * gated until the stream settles (or the user clicks Stop).
 */
export function ChatShell() {
  const streams = useConversationStreams();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvoId, setActiveConvoId] = useState<string | null>(null);

  // Archive-dedupe: tracks which `${convoId}:${exchange.id}` pairs we've
  // already appended to a conversation's `exchanges`. Without this the
  // archive effect would re-append on every render. Set semantics
  // because lookups must be O(1) over potentially many concurrent
  // streams.
  const archivedKeysRef = useRef<Set<string>>(new Set());

  // Layout persistence ---------------------------------------------------
  const [sidebarCollapsed, setSidebarCollapsed] = usePersistedState<boolean>(
    STORAGE_SIDEBAR_COLLAPSED,
    false,
  );
  const [evidenceWidth, setEvidenceWidth] = usePersistedState<number>(
    STORAGE_EVIDENCE_WIDTH,
    EVIDENCE_DEFAULT_WIDTH,
  );

  // Archive freshly-completed (or interrupted) exchanges into their
  // respective conversations. Iterates over every stream so we catch
  // completions even for conversations the user isn't currently
  // viewing. Slice 5.0c-k.
  useEffect(() => {
    for (const [convoId, state] of Object.entries(streams.streams)) {
      if (!state.exchange) continue;
      const key = `${convoId}:${state.exchange.id}`;
      if (archivedKeysRef.current.has(key)) continue;
      archivedKeysRef.current.add(key);
      const ex = state.exchange;
      setConversations((prev) =>
        prev.map((c) =>
          c.id === convoId
            ? {
                ...c,
                exchanges: [...c.exchanges, ex],
                updated_at: ex.completed_at,
              }
            : c,
        ),
      );
    }
  }, [streams.streams]);

  const activeConversation =
    conversations.find((c) => c.id === activeConvoId) ?? null;
  const activeStream =
    activeConvoId !== null
      ? streams.streams[activeConvoId] ?? NEUTRAL_STREAM
      : NEUTRAL_STREAM;
  const isActiveStreaming = activeStream.status.phase === "running";

  const handleSubmit = useCallback(
    async (question: string) => {
      // Determine the target conversation: existing active, or create
      // a new one synchronously so it appears in the sidebar the moment
      // the user submits.
      let targetConvoId = activeConvoId;
      let history: ConversationTurn[] = [];
      if (targetConvoId) {
        // Guard against re-submit while THIS conversation is streaming
        // (the Send button is also disabled in that state, but this
        // catches keyboard-Enter races).
        if (streams.runningIds.has(targetConvoId)) return;
        const c = conversations.find((c) => c.id === targetConvoId);
        history = c
          ? c.exchanges.flatMap((ex) => [
              { role: "user" as const, content: ex.question },
              { role: "assistant" as const, content: ex.answer },
            ])
          : [];
      } else {
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
      await streams.submit(targetConvoId, question, history);
    },
    [activeConvoId, conversations, streams],
  );

  // Slice 5.0c-k: Stop button for the currently-active conversation.
  // No-op if the active conversation isn't streaming.
  const handleStop = useCallback(() => {
    if (activeConvoId !== null) streams.stop(activeConvoId);
  }, [activeConvoId, streams]);

  const handleNew = useCallback(() => {
    // No need to abort other conversations' streams — each has its own
    // state and AbortController. Just navigate to the empty greeting.
    setActiveConvoId(null);
  }, []);

  const handleSelect = useCallback((id: string) => {
    setActiveConvoId(id);
  }, []);

  // Slice 5.0c-i: conversation rename. Both the top-bar title and the
  // sidebar's per-item "…" menu route here.
  const handleRename = useCallback((id: string, nextTitle: string) => {
    const trimmed = nextTitle.trim();
    if (!trimmed) return;
    setConversations((prev) =>
      prev.map((c) =>
        c.id === id ? { ...c, title: trimmed.slice(0, 80) } : c,
      ),
    );
  }, []);

  // Slice 5.0c-i.2: star / unstar.
  const handleToggleStar = useCallback((id: string) => {
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, starred: !c.starred } : c)),
    );
  }, []);

  // Slice 5.0c-i.3: pending-delete state for the app-native dialog.
  const [pendingDelete, setPendingDelete] = useState<{
    ids: string[];
    titles: string[];
  } | null>(null);

  const handleDelete = useCallback(
    (id: string) => {
      // Refuse if this conversation is currently streaming — the
      // archive effect would otherwise write to a removed slot. The
      // sidebar / overlay menus also disable Delete for streaming
      // conversations, but belt-and-braces here.
      if (streams.runningIds.has(id)) return;
      const target = conversations.find((c) => c.id === id);
      if (!target) return;
      setPendingDelete({ ids: [id], titles: [target.title] });
    },
    [streams.runningIds, conversations],
  );

  const handleBulkDelete = useCallback(
    (ids: string[]) => {
      const safeIds = ids.filter((id) => !streams.runningIds.has(id));
      if (safeIds.length === 0) return;
      const titles = safeIds
        .map((id) => conversations.find((c) => c.id === id)?.title)
        .filter((t): t is string => typeof t === "string");
      setPendingDelete({ ids: safeIds, titles });
    },
    [streams.runningIds, conversations],
  );

  const confirmPendingDelete = useCallback(() => {
    if (!pendingDelete) return;
    const ids = pendingDelete.ids;
    setConversations((prev) => prev.filter((c) => !ids.includes(c.id)));
    // Drop the stream state for each deleted conversation so the hook
    // doesn't keep stale buffers around. `reset` is a no-op for unknown
    // ids, safe to call on every deletion.
    for (const id of ids) {
      streams.reset(id);
    }
    if (activeConvoId && ids.includes(activeConvoId)) {
      setActiveConvoId(null);
    }
    setPendingDelete(null);
  }, [pendingDelete, activeConvoId, streams]);

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

  // Composer draft handoff (Slice 5.0c-f).
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
  const handleAssistantRetry = useCallback(
    async (originatingQuestion: string) => {
      if (!activeConversation) return;
      // Refuse if this conversation is currently streaming — the user
      // should stop it first before kicking off a Retry.
      if (streams.runningIds.has(activeConversation.id)) return;
      const history: ConversationTurn[] =
        activeConversation.exchanges.flatMap((ex) => [
          { role: "user" as const, content: ex.question },
          { role: "assistant" as const, content: ex.answer },
        ]);
      await streams.submit(
        activeConversation.id,
        originatingQuestion,
        history,
        { retryNudge: true },
      );
    },
    [activeConversation, streams],
  );

  // Evidence panel: prefer in-flight stream citations when the active
  // conversation is the one streaming. Otherwise fall back to the
  // latest archived exchange's evidence.
  const visibleExchanges: ChatExchange[] =
    activeConversation?.exchanges ?? [];
  const latestArchived: ChatExchange | null =
    visibleExchanges.length > 0
      ? visibleExchanges[visibleExchanges.length - 1]
      : null;
  const citations: Citation[] =
    isActiveStreaming && activeStream.citations.length > 0
      ? activeStream.citations
      : (latestArchived?.citations ?? []);
  const toolEvents: ToolEvent[] =
    isActiveStreaming && activeStream.toolEvents.length > 0
      ? activeStream.toolEvents
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
          streamingIds={streams.runningIds}
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
            stream={activeStream}
            onEdit={setDraft}
            onRetry={setDraft}
            onQuickAsk={setDraft}
            onAssistantRetry={handleAssistantRetry}
          />
          <div className="shrink-0 border-t border-border bg-card/50 px-4 pt-3 pb-2">
            <ChatComposer
              onSubmit={handleSubmit}
              streaming={isActiveStreaming}
              onStop={handleStop}
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
      <ChatsHistoryOverlay
        open={chatsOverlayOpen}
        conversations={sortedConversations}
        streamingIds={streams.runningIds}
        onClose={() => setChatsOverlayOpen(false)}
        onSelect={handleSelect}
        onNew={handleNew}
        onRename={handleRename}
        onToggleStar={handleToggleStar}
        onBulkDelete={handleBulkDelete}
      />
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
