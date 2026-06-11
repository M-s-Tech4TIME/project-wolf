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
import {
  activeLeaf,
  activePathNodes,
  appendChildOf,
  fork,
  historyUpTo,
  makeAssistantNode,
  makeUserNode,
  selectPathTo,
  switchToSibling,
} from "@/lib/branches";
import type {
  AssistantMessageNode,
  Citation,
  Conversation,
  ConversationTurn,
  MessageNode,
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
 *  │ Header: app + organization switcher + user-avatar dropdown        │
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

  // Archive freshly-completed (or interrupted) stream completions
  // into their respective conversations. Iterates over every stream
  // so we catch completions even for conversations the user isn't
  // currently viewing.
  //
  // Slice 5.0c-l (node-tree refactor): each completion produces a
  // single AssistantMessageNode appended to the user-message node
  // identified by `completion.parent_user_node_id`. The user node
  // itself was already added to the tree synchronously at submit /
  // save / retry time, so this archive layer never creates user
  // nodes — only assistant ones. The bug we're fixing (cross-fork
  // merge into a "3/3" sibling set) is impossible here because the
  // append goes to `nodes[parent_user_node_id].children` and nothing
  // else.
  useEffect(() => {
    for (const [convoId, state] of Object.entries(streams.streams)) {
      const completion = state.completion;
      if (!completion) continue;
      const key = `${convoId}:${completion.id}`;
      if (archivedKeysRef.current.has(key)) continue;
      archivedKeysRef.current.add(key);
      setConversations((prev) =>
        prev.map((c) => {
          if (c.id !== convoId) return c;
          const parent_user_node = c.nodes[completion.parent_user_node_id];
          // Defensive guard: if the user node disappeared (e.g. the
          // conversation was reset mid-stream), drop the completion
          // silently rather than crashing.
          if (!parent_user_node || parent_user_node.role !== "user") {
            return c;
          }
          const assistant_node: AssistantMessageNode = makeAssistantNode({
            id: completion.id,
            parent_user_node_id: completion.parent_user_node_id,
            content: completion.content,
            citations: completion.citations,
            tool_events: completion.tool_events,
            stop_reason: completion.stop_reason,
            loop_id: completion.loop_id,
            strategy: completion.strategy,
            model_id: completion.model_id,
            step_count: completion.step_count,
            tool_call_count: completion.tool_call_count,
            input_tokens: completion.input_tokens,
            output_tokens: completion.output_tokens,
            started_at: completion.started_at,
            completed_at: completion.completed_at,
            grounding_supported: completion.grounding_supported,
            grounding_unsupported: completion.grounding_unsupported,
            grounding_uncertain: completion.grounding_uncertain,
            grounding_unverifiable: completion.grounding_unverifiable,
          });
          const next = appendChildOf(
            c,
            completion.parent_user_node_id,
            assistant_node,
          );
          return { ...next, updated_at: completion.completed_at };
        }),
      );
      // Slice 5.0c-l v4.1: the completion has been consumed. Clear
      // the stream's `completion` field so the render-time filter
      // in MessageThread (which uses `completion !== null` as part
      // of its "should I hide the prior sibling?" predicate)
      // doesn't re-fire on later navigate-back clicks. Without this
      // step the prior assistant sibling would still render empty
      // after the user uses the `<` navigator to return to it —
      // even though its data is intact in `conversation.nodes`.
      streams.clearCompletion(convoId);
    }
    // `streams.clearCompletion` is wrapped in useCallback with no
    // deps — referentially stable, so we deliberately depend only
    // on the streams state record (`streams.streams`). Listing the
    // whole `streams` object would refire this effect every render
    // since the hook returns a fresh wrapper each time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      const trimmed = question.trim();
      if (!trimmed) return;
      const now = new Date().toISOString();
      const newUserNodeId = randomId();

      // Slice 5.0c-l (node-tree refactor): fresh turn at the tip.
      // The user-message node is created HERE, synchronously, so the
      // tree is fully consistent before the stream starts. The
      // assistant node is appended in the archive effect once the
      // run settles. Both never collapse into a single Q+A unit.
      //
      // Parent rules:
      //   - Empty conversation       → user node is a root (parent_id null).
      //   - Existing conversation     → user node is appended under the
      //     active LEAF, which is always an assistant node when the
      //     prior turn settled. Strict alternation along the path.
      let targetConvoId = activeConvoId;
      let history: ConversationTurn[] = [];

      if (targetConvoId) {
        if (streams.runningIds.has(targetConvoId)) return;
        const c = conversations.find((c) => c.id === targetConvoId);
        if (!c) return;
        const leaf = activeLeaf(c);
        // The new user message hangs off the current leaf. For a
        // freshly-created conversation that ran zero turns this leaf
        // could be a user node with no children — defensive: still
        // OK because the new turn becomes its child and the tree
        // alternates again from there.
        const userNode = makeUserNode({
          id: newUserNodeId,
          parent_id: leaf?.id ?? null,
          content: trimmed,
          created_at: now,
        });
        history = activePathNodes(c).map((n) => ({
          role: n.role,
          content: n.content,
        }));
        setConversations((prev) =>
          prev.map((conv) =>
            conv.id === targetConvoId
              ? appendChildOf(conv, leaf?.id ?? null, userNode)
              : conv,
          ),
        );
      } else {
        const newConvo: Conversation = {
          id: randomId(),
          title: titleFromQuestion(question),
          nodes: {},
          root_children: [],
          selected_root_id: null,
          created_at: now,
          updated_at: now,
        };
        const userNode = makeUserNode({
          id: newUserNodeId,
          parent_id: null,
          content: trimmed,
          created_at: now,
        });
        const seeded = appendChildOf(newConvo, null, userNode);
        setConversations((prev) => [seeded, ...prev]);
        targetConvoId = newConvo.id;
        setActiveConvoId(targetConvoId);
      }

      await streams.submit(targetConvoId, trimmed, history, newUserNodeId);
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

  // Slice 5.0c-l v4: history-overlay variant of select. If the user
  // clicked a search match, `matched_node_id` points at the node
  // the match came from — possibly off the active branch. We
  // re-point every selected-child pointer along the chain root→
  // target so the match surfaces the moment the overlay closes.
  // Other branch points off the chain keep their own selections.
  const handleSelectFromHistory = useCallback(
    (id: string, matched_node_id: string | null) => {
      if (matched_node_id) {
        setConversations((prev) =>
          prev.map((c) => (c.id === id ? selectPathTo(c, matched_node_id) : c)),
        );
      }
      setActiveConvoId(id);
    },
    [],
  );

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

  // Slice 5.0c-l v4: Retry an assistant message.
  //
  //   fork(targetAssistantNode):
  //     parent  = targetAssistantNode.parent_id     // a USER node
  //     newAsst = AssistantMessageNode (filled in by the archive
  //               effect once the stream settles)
  //     append newAsst to parent.children          // and select it
  //
  // The new assistant becomes a sibling of the target — `< N/M >`
  // navigator appears at the assistant-message position because the
  // user-node parent's `children.length` is now > 1. The retry's
  // critique target is whichever sibling was on the active path at
  // submit time, since that's what `historyUpTo` walked.
  //
  // The new user node is NOT created here — the target's user
  // parent already exists and we hang the new assistant off it.
  const handleRetryAssistant = useCallback(
    async (target_assistant_id: string) => {
      if (!activeConversation) return;
      if (streams.runningIds.has(activeConversation.id)) return;
      const target = activeConversation.nodes[target_assistant_id];
      if (!target || target.role !== "assistant") return;
      const parentUserNode = activeConversation.nodes[target.parent_id];
      if (!parentUserNode || parentUserNode.role !== "user") return;
      const history = historyUpTo(activeConversation, target.id);
      await streams.submit(
        activeConversation.id,
        parentUserNode.content,
        history,
        parentUserNode.id,
        { retryNudge: true },
      );
    },
    [activeConversation, streams],
  );

  // Slice 5.0c-l v4: Edit a user message.
  //
  //   fork(targetUserNode):
  //     parent  = targetUserNode.parent_id    // assistant OR root (null)
  //     newUser = UserMessageNode(edited)
  //     append newUser to parent.children     // and select it
  //   then start the stream so a new assistant child of newUser
  //   gets generated and appended in the archive effect.
  //
  // The new user-sibling lives in `parent.children` — a DIFFERENT
  // children array from `targetUserNode.children` (which holds the
  // original's assistant siblings). This is the structural fix for
  // the cross-fork merge bug.
  const handleEditUserMessage = useCallback(
    async (target_user_id: string, edited_question: string) => {
      if (!activeConversation) return;
      if (streams.runningIds.has(activeConversation.id)) return;
      const target = activeConversation.nodes[target_user_id];
      if (!target || target.role !== "user") return;
      const trimmed = edited_question.trim();
      if (!trimmed) return;
      const now = new Date().toISOString();
      const newUserNodeId = randomId();
      const newUserNode = makeUserNode({
        id: newUserNodeId,
        parent_id: target.parent_id,
        content: trimmed,
        created_at: now,
      });
      // Build the history BEFORE we mutate, walking the path up to
      // (but excluding) the target — same context the backend would
      // have seen for the original turn.
      const history = historyUpTo(activeConversation, target.id);
      // Append the new user sibling to target.parent_id's children
      // (the one and only array the fork primitive ever touches).
      setConversations((prev) =>
        prev.map((c) =>
          c.id === activeConversation.id ? fork(c, target.id, newUserNode) : c,
        ),
      );
      await streams.submit(
        activeConversation.id,
        trimmed,
        history,
        newUserNodeId,
      );
    },
    [activeConversation, streams],
  );

  // Slice 5.0c-l v4: navigator click — re-point the active branch at
  // a fork to `new_sibling_id`. Both ids share a parent, and only
  // that parent's `selected_child_id` (or `selected_root_id` for
  // top-level forks) changes. Every other branch point's selection
  // along the path stays intact.
  const handleSwitchBranch = useCallback(
    (current_id: string, new_sibling_id: string) => {
      const convoId = activeConversation?.id;
      if (!convoId) return;
      setConversations((prev) =>
        prev.map((c) =>
          c.id === convoId ? switchToSibling(c, current_id, new_sibling_id) : c,
        ),
      );
    },
    [activeConversation],
  );

  // Evidence panel: prefer in-flight stream citations when the active
  // conversation is the one streaming. Otherwise fall back to the
  // latest assistant node on the visible branch — siblings on other
  // branches stay in `nodes` but should not influence the evidence
  // panel for the current view.
  const visibleNodes: MessageNode[] = activeConversation
    ? activePathNodes(activeConversation)
    : [];
  const latestAssistant: AssistantMessageNode | null = (() => {
    for (let i = visibleNodes.length - 1; i >= 0; i--) {
      const n = visibleNodes[i];
      if (n.role === "assistant") return n;
    }
    return null;
  })();
  const citations: Citation[] =
    isActiveStreaming && activeStream.citations.length > 0
      ? activeStream.citations
      : (latestAssistant?.citations ?? []);
  const toolEvents: ToolEvent[] =
    isActiveStreaming && activeStream.toolEvents.length > 0
      ? activeStream.toolEvents
      : (latestAssistant?.tool_events ?? []);

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
            conversation={activeConversation}
            stream={activeStream}
            onEditUserMessage={handleEditUserMessage}
            onRetryAssistant={handleRetryAssistant}
            onSwitchBranch={handleSwitchBranch}
            onQuickAsk={setDraft}
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
        onSelect={handleSelectFromHistory}
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
