"use client";

import { useCallback, useMemo, useRef, useState } from "react";

import { phraseFor } from "@/lib/activity-phrases";
import { ApiError, chatStream } from "@/lib/api";
import type {
  Citation,
  ConversationTurn,
  LoopEvent,
  StreamCompletion,
  ToolEvent,
} from "@/lib/types";
import { randomId } from "@/lib/uuid";

export type StreamPhase = "idle" | "running" | "done" | "error";

/**
 * ADR 0026 — a late grounding result for a SETTLED assistant message
 * (deferred/incremental modes). The `answer` event archives the node before
 * the judge runs; this carries the verdicts that chat-shell then patches onto
 * the archived node (matched by `loop_id`). `ran === false` means the judge
 * failed — clear the pending indicator, keep the raw content. For incremental
 * mode each `grounding.partial` overwrites this with a cumulative snapshot;
 * the final `grounding.completed` carries the complete result.
 */
export type GroundingPatch = {
  loop_id: string;
  ran: boolean;
  content: string;
  supported: number;
  unsupported: number;
  uncertain: number;
  unverifiable: number;
};

export type StreamStatus = {
  phase: StreamPhase;
  loop_id?: string;
  strategy?: string;
  model_id?: string;
  step_budget?: number;
  step?: number;
  last_event_type?: LoopEvent["type"];
  message?: string;
};

/**
 * The displayable state for one conversation's in-flight (or last
 * settled) chat stream. Mirrors the previous singleton-hook state
 * shape, now keyed per conversation.
 */
export type StreamState = {
  status: StreamStatus;
  /**
   * Slice 5.0c-l (node-tree refactor): the assistant-side completion
   * payload — chat-shell's archive layer converts this into an
   * `AssistantMessageNode` and appends it as a child of
   * `parent_user_node_id`. The user message itself is created
   * synchronously by chat-shell at submit/save time, not by this
   * hook.
   */
  completion: StreamCompletion | null;
  toolEvents: ToolEvent[];
  citations: Citation[];
  error: string | null;
  currentQuestion: string;
  streamingAnswer: string;
  /**
   * Slice 5.0c-l: the user message node id this in-flight run
   * answers. Always set when a stream is running (the user node
   * was created synchronously before submit). MessageThread reads
   * this so the streaming view's activity feed can render in the
   * assistant slot directly under the right user message.
   */
  parentUserNodeId: string | null;
  /**
   * ADR 0026 — the latest late-grounding result for this conversation's
   * settled message, or null. chat-shell applies it to the archived node
   * (by loop_id) then calls `clearGroundingPatch`.
   */
  groundingPatch: GroundingPatch | null;
};

/**
 * Default state returned for any conversation the hook hasn't seen
 * yet. Exported so consumers can use it as a stable fallback when
 * reading from `streams[convoId] ?? NEUTRAL_STREAM`.
 */
export const NEUTRAL_STREAM: StreamState = {
  status: { phase: "idle" },
  completion: null,
  toolEvents: [],
  citations: [],
  error: null,
  currentQuestion: "",
  streamingAnswer: "",
  parentUserNodeId: null,
  groundingPatch: null,
};

export type UseConversationStreams = {
  /** Stream state by conversation ID. Consumers read
   *  `streams[convoId] ?? NEUTRAL_STREAM`. */
  streams: Record<string, StreamState>;
  /** Conversation IDs that are currently in `phase: "running"`. Set
   *  semantics so the sidebar can do `has(id)` per row in O(1). */
  runningIds: Set<string>;
  /** Start a stream for `convoId`. Refuses (no-op) if the same
   *  conversation already has a stream in flight; the caller should
   *  gate UI accordingly. Different conversations can stream
   *  simultaneously.
   *
   *  Slice 5.0c-l (node-tree refactor): the caller (chat-shell)
   *  creates the user-message node synchronously BEFORE calling
   *  submit and passes its id as `parentUserNodeId`. The hook
   *  stamps that id onto the completion payload (success or
   *  interrupted), so the archive layer always attaches the new
   *  assistant node to the exact right user parent. */
  submit: (
    convoId: string,
    question: string,
    history: ConversationTurn[],
    parentUserNodeId: string,
    opts?: { retryNudge?: boolean },
  ) => Promise<void>;
  /** Abort the in-flight stream for `convoId` (if any). The fetch
   *  rejects, the catch path synthesises a ChatExchange with
   *  `stop_reason: "interrupted"` containing whatever partial answer
   *  + tool events were collected before the abort. */
  stop: (convoId: string) => void;
  /** Drop all state for `convoId` (aborts an in-flight stream first
   *  if one exists). Used when a conversation is deleted. */
  reset: (convoId: string) => void;
  /** Slice 5.0c-l v4.1: clear ONLY the `completion` field of the
   *  named conversation's stream state. Called by chat-shell right
   *  after the archive effect appends the new assistant node, so
   *  no stale completion data lingers to drive render-time filters
   *  (the bug that hid earlier siblings' content on navigate-back).
   *  Leaves all other fields intact so the user's "Stop" / Retry
   *  affordances on the new assistant remain wired up. */
  clearCompletion: (convoId: string) => void;
};

function asString(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

function asNumber(v: unknown, fallback = 0): number {
  return typeof v === "number" ? v : fallback;
}

function asNullableNumber(v: unknown): number | null {
  return typeof v === "number" ? v : null;
}

/**
 * Per-conversation chat-stream manager. Slice 5.0c-k.
 *
 * Replaces the previous singleton `useChatStream` (one in-flight
 * stream per app). Each conversation now has its own independent
 * StreamState + AbortController, so the user can:
 *
 *   - Stream multiple conversations at once.
 *   - Click "Stop" on a running stream and resume drafting in the
 *     same conversation.
 *
 * Design notes:
 *
 *   - State for all known conversations lives in ONE useState
 *     (`Record<convoId, StreamState>`). Each SSE event flushes
 *     through a single functional setState, so callback identity
 *     stays stable (empty `useCallback` deps) and callers don't
 *     re-bind on every status change.
 *   - Per-conversation working buffers live in refs (controllers,
 *     streaming-answer accumulator, tool events, citations,
 *     started_at, question). The refs let event handlers read the
 *     freshest accumulated value without waiting for React state
 *     batching to flush — same pattern as the old singleton hook.
 *   - "Is this conversation running?" check is *not* a state read
 *     but a ref read (`controllersRef.current[convoId]` defined ⇔
 *     in flight). That makes the submit guard race-free without
 *     needing `streams` in the callback's deps.
 *   - On AbortError (Stop button), the catch block synthesises a
 *     ChatExchange with stop_reason="interrupted" containing the
 *     partial answer + accumulated tool events + citations. Consumers
 *     archive this exchange like any other; MessageThread renders a
 *     small "Response interrupted by user" footer when it sees the
 *     "interrupted" stop_reason.
 */
export function useConversationStreams(): UseConversationStreams {
  const [streams, setStreams] = useState<Record<string, StreamState>>({});

  // Per-conversation working buffers.
  const controllersRef = useRef<Record<string, AbortController>>({});
  const streamingAnswerRef = useRef<Record<string, string>>({});
  const toolEventsRef = useRef<Record<string, ToolEvent[]>>({});
  const citationsRef = useRef<Record<string, Citation[]>>({});
  const questionRef = useRef<Record<string, string>>({});
  const startedAtRef = useRef<Record<string, string>>({});
  // Slice 5.0c-l (node-tree refactor): the user-message node id this
  // run answers. Captured at submit time so the synthesised/completed
  // StreamCompletion can stamp `parent_user_node_id` correctly
  // without leaking tree concerns into the SSE event handlers.
  const parentUserNodeIdRef = useRef<Record<string, string>>({});

  // Internal helpers — closure over the stable refs / state setter, so
  // we don't have to thread these through every event-handler case.
  const updateStream = useCallback(
    (convoId: string, updater: (prev: StreamState) => StreamState): void => {
      setStreams((prev) => ({
        ...prev,
        [convoId]: updater(prev[convoId] ?? NEUTRAL_STREAM),
      }));
    },
    [],
  );
  const updateStatus = useCallback(
    (convoId: string, updater: (prev: StreamStatus) => StreamStatus): void => {
      updateStream(convoId, (s) => ({ ...s, status: updater(s.status) }));
    },
    [updateStream],
  );

  const submit = useCallback(
    async (
      convoId: string,
      question: string,
      history: ConversationTurn[],
      parentUserNodeId: string,
      opts: { retryNudge?: boolean } = {},
    ): Promise<void> => {
      // Refuse if this conversation already has a stream in flight.
      // The controller ref is the in-flight signal — set here on
      // submit, cleared in the `finally` block. Race-free w/o needing
      // a state dep on `streams`.
      if (controllersRef.current[convoId]) return;
      const trimmed = question.trim();
      if (!trimmed) return;

      const controller = new AbortController();
      controllersRef.current[convoId] = controller;

      // Reset working buffers for this conversation.
      streamingAnswerRef.current[convoId] = "";
      toolEventsRef.current[convoId] = [];
      citationsRef.current[convoId] = [];
      questionRef.current[convoId] = trimmed;
      startedAtRef.current[convoId] = new Date().toISOString();
      parentUserNodeIdRef.current[convoId] = parentUserNodeId;

      // Initialise displayable state.
      updateStream(convoId, () => ({
        status: { phase: "running", last_event_type: undefined },
        completion: null,
        toolEvents: [],
        citations: [],
        error: null,
        currentQuestion: trimmed,
        streamingAnswer: "",
        parentUserNodeId,
        groundingPatch: null,
      }));

      try {
        await chatStream(
          {
            question: trimmed,
            history,
            retry_nudge: opts.retryNudge ?? false,
          },
          (event) => {
            switch (event.type) {
              case "loop.started": {
                updateStatus(convoId, (s) => ({
                  ...s,
                  phase: "running",
                  loop_id: asString(event.data.loop_id),
                  strategy: asString(event.data.strategy),
                  model_id: asString(event.data.model_id),
                  step_budget: asNumber(event.data.step_budget),
                  last_event_type: event.type,
                  message: `Starting (${asString(event.data.strategy)}, ${asString(event.data.model_id)})`,
                }));
                break;
              }
              case "step.started": {
                streamingAnswerRef.current[convoId] = "";
                updateStream(convoId, (s) => ({
                  ...s,
                  streamingAnswer: "",
                  status: {
                    ...s.status,
                    step: asNumber(event.data.step),
                    last_event_type: event.type,
                    message: phraseFor("step.started", {
                      step: asNumber(event.data.step),
                      budget: s.status.step_budget,
                    }),
                  },
                }));
                break;
              }
              case "model.delta": {
                const delta = asString(event.data.content_delta);
                if (delta) {
                  streamingAnswerRef.current[convoId] =
                    (streamingAnswerRef.current[convoId] ?? "") + delta;
                  const next = streamingAnswerRef.current[convoId];
                  updateStream(convoId, (s) => ({
                    ...s,
                    streamingAnswer: next,
                  }));
                }
                break;
              }
              case "model.call.completed": {
                const toolCount = asNumber(event.data.tool_call_count);
                updateStatus(convoId, (s) => ({
                  ...s,
                  last_event_type: event.type,
                  message:
                    toolCount > 0
                      ? phraseFor("model.call.completed.tools", { n: toolCount })
                      : phraseFor("model.call.completed.answer", {}),
                }));
                break;
              }
              case "model.call.failed": {
                updateStatus(convoId, (s) => ({
                  ...s,
                  last_event_type: event.type,
                  message: phraseFor("model.call.failed", {
                    detail: asString(event.data.detail, "unknown error"),
                  }),
                }));
                break;
              }
              case "tool.call.started": {
                updateStatus(convoId, (s) => ({
                  ...s,
                  last_event_type: event.type,
                  message: phraseFor("tool.call.started", {
                    tool: asString(event.data.tool_name),
                  }),
                }));
                break;
              }
              case "tool.call.completed": {
                const tool: ToolEvent = {
                  tool_name: asString(event.data.tool_name),
                  tool_call_id: asString(event.data.tool_call_id),
                  success: Boolean(event.data.success),
                  elapsed_ms: asNumber(event.data.elapsed_ms),
                  citation: event.data.citation as Citation | undefined,
                  counts: event.data.counts as
                    | Record<string, number>
                    | undefined,
                  error:
                    typeof event.data.error === "string"
                      ? event.data.error
                      : undefined,
                };
                toolEventsRef.current[convoId] = [
                  ...(toolEventsRef.current[convoId] ?? []),
                  tool,
                ];
                if (tool.citation) {
                  citationsRef.current[convoId] = [
                    ...(citationsRef.current[convoId] ?? []),
                    tool.citation,
                  ];
                }
                updateStream(convoId, (s) => ({
                  ...s,
                  toolEvents: toolEventsRef.current[convoId] ?? [],
                  citations: citationsRef.current[convoId] ?? [],
                  status: {
                    ...s.status,
                    last_event_type: event.type,
                    message: tool.success
                      ? phraseFor("tool.call.completed.ok", {
                          tool: tool.tool_name,
                          elapsed: tool.elapsed_ms,
                        })
                      : phraseFor("tool.call.completed.fail", {
                          tool: tool.tool_name,
                          error: tool.error ?? "unknown",
                        }),
                  },
                }));
                break;
              }
              case "grounding.started": {
                updateStatus(convoId, (s) => ({
                  ...s,
                  last_event_type: event.type,
                  message: phraseFor("grounding.started", {}),
                }));
                break;
              }
              // ADR 0026 — both events share a handler. `grounding.partial`
              // (incremental) and `grounding.completed` in deferred/incremental
              // carry `annotated_content` → patch the SETTLED message's chips
              // in place. blocking's `grounding.completed` omits it (the
              // `answer` event already holds the annotated content + counts),
              // so it only refreshes the activity status line.
              case "grounding.partial":
              case "grounding.completed": {
                const sup = asNumber(event.data.supported);
                const uncertain = asNumber(event.data.uncertain);
                const unsup = asNumber(event.data.unsupported);
                const unverifiable = asNumber(event.data.unverifiable);
                const ran = Boolean(event.data.ran);
                const annotated = event.data.annotated_content;
                if (typeof annotated === "string") {
                  const patch: GroundingPatch = {
                    loop_id: asString(event.data.loop_id),
                    ran,
                    content: annotated,
                    supported: sup,
                    unsupported: unsup,
                    uncertain,
                    unverifiable,
                  };
                  updateStream(convoId, (s) => ({ ...s, groundingPatch: patch }));
                }
                if (ran && event.type === "grounding.completed") {
                  updateStatus(convoId, (s) => ({
                    ...s,
                    last_event_type: event.type,
                    message: phraseFor("grounding.completed", {
                      sup,
                      uncertain,
                      unsup,
                    }),
                  }));
                }
                break;
              }
              case "answer": {
                const data = event.data;
                const completedAt = new Date().toISOString();
                const backendCitations =
                  (data.citations as Citation[] | undefined) ?? [];
                const finalCitations =
                  backendCitations.length > 0
                    ? backendCitations
                    : citationsRef.current[convoId] ?? [];
                const completed: StreamCompletion = {
                  // Slice 5.0c-l v4.1: node ids are always generated
                  // client-side and never reuse loop_id. Loop ids are
                  // unique per wolf-server run today, but coupling
                  // node-identity to a backend-issued field made the
                  // tree's uniqueness guarantee depend on the
                  // backend's. A randomId() here means a future
                  // backend regression (e.g. echoing a cached
                  // loop_id) can never collide with an existing node
                  // and overwrite its content via the spread in
                  // `appendChildOf`.
                  id: randomId(),
                  parent_user_node_id: parentUserNodeIdRef.current[convoId],
                  content: asString(data.content),
                  citations: finalCitations,
                  tool_events: toolEventsRef.current[convoId] ?? [],
                  stop_reason:
                    (data.stop_reason as StreamCompletion["stop_reason"]) ??
                    "answer",
                  loop_id: asString(data.loop_id),
                  strategy: asString(data.strategy),
                  model_id: asString(data.model_id),
                  step_count: asNumber(data.step_count),
                  tool_call_count: asNumber(data.tool_call_count),
                  input_tokens: asNumber(data.input_tokens),
                  output_tokens: asNumber(data.output_tokens),
                  started_at: startedAtRef.current[convoId] ?? completedAt,
                  completed_at: completedAt,
                  grounding_supported: asNullableNumber(
                    data.grounding_supported,
                  ),
                  grounding_unsupported: asNullableNumber(
                    data.grounding_unsupported,
                  ),
                  grounding_uncertain: asNullableNumber(
                    data.grounding_uncertain,
                  ),
                  grounding_unverifiable: asNullableNumber(
                    data.grounding_unverifiable,
                  ),
                  // ADR 0026 — deferred/incremental: the answer settled before
                  // the verdicts; render a "Verifying claims…" indicator until
                  // the late grounding event patches in the chips.
                  grounding_pending: Boolean(data.grounding_pending),
                };
                citationsRef.current[convoId] = finalCitations;
                streamingAnswerRef.current[convoId] = "";
                updateStream(convoId, (s) => ({
                  ...s,
                  completion: completed,
                  citations: finalCitations,
                  streamingAnswer: "",
                  status: {
                    ...s.status,
                    phase: "done",
                    last_event_type: event.type,
                    message: undefined,
                  },
                }));
                break;
              }
            }
          },
          controller.signal,
        );
      } catch (err) {
        // Distinguish "user clicked Stop" from real failures via the
        // controller's own `signal.aborted`. Don't rely on err.name.
        if (controller.signal.aborted) {
          const completedAt = new Date().toISOString();
          const partialAnswer = streamingAnswerRef.current[convoId] ?? "";
          // Synthesise an interrupted StreamCompletion. Token counts
          // / step counts default to 0 because we don't have the
          // backend's tallies; tool_call_count reflects how many
          // tools the agent had actually completed before the abort.
          const interrupted: StreamCompletion = {
            id: randomId(),
            parent_user_node_id: parentUserNodeIdRef.current[convoId],
            content: partialAnswer,
            citations: citationsRef.current[convoId] ?? [],
            tool_events: toolEventsRef.current[convoId] ?? [],
            stop_reason: "interrupted",
            loop_id: "",
            strategy: "",
            model_id: "",
            step_count: 0,
            tool_call_count: (toolEventsRef.current[convoId] ?? []).length,
            input_tokens: 0,
            output_tokens: 0,
            started_at: startedAtRef.current[convoId] ?? completedAt,
            completed_at: completedAt,
            grounding_supported: null,
            grounding_unsupported: null,
            grounding_uncertain: null,
            grounding_unverifiable: null,
            grounding_pending: false,
          };
          streamingAnswerRef.current[convoId] = "";
          updateStream(convoId, (s) => ({
            ...s,
            completion: interrupted,
            streamingAnswer: "",
            error: null,
            status: { ...s.status, phase: "done", message: undefined },
          }));
        } else {
          const msg =
            err instanceof ApiError
              ? `${err.status}: ${err.message}`
              : err instanceof Error
                ? err.message
                : "Streaming failed";
          updateStream(convoId, (s) => ({
            ...s,
            error: msg,
            status: { ...s.status, phase: "error", message: msg },
          }));
        }
      } finally {
        // Always clear the controller — the next submit for this
        // conversation should be free to start. We deliberately keep
        // the working refs (answer/tools/citations) around in case
        // chat-shell is still archiving the just-finished exchange.
        delete controllersRef.current[convoId];
      }
    },
    [updateStream, updateStatus],
  );

  const stop = useCallback((convoId: string): void => {
    const controller = controllersRef.current[convoId];
    if (controller && !controller.signal.aborted) {
      controller.abort();
    }
  }, []);

  const reset = useCallback((convoId: string): void => {
    const controller = controllersRef.current[convoId];
    if (controller && !controller.signal.aborted) {
      controller.abort();
    }
    delete controllersRef.current[convoId];
    delete streamingAnswerRef.current[convoId];
    delete toolEventsRef.current[convoId];
    delete citationsRef.current[convoId];
    delete questionRef.current[convoId];
    delete startedAtRef.current[convoId];
    delete parentUserNodeIdRef.current[convoId];
    setStreams((prev) => {
      const next = { ...prev };
      delete next[convoId];
      return next;
    });
  }, []);

  // Slice 5.0c-l v4.1: clear the just-archived completion so the
  // truncation filter in MessageThread (which keys off `completion`)
  // doesn't re-fire on subsequent navigate-back. The hook stays
  // otherwise stateful — running guards, abort controllers, etc. —
  // because the archive layer can pre-empt new submits via the
  // controller ref.
  const clearCompletion = useCallback((convoId: string): void => {
    setStreams((prev) => {
      const state = prev[convoId];
      if (!state || state.completion === null) return prev;
      return { ...prev, [convoId]: { ...state, completion: null } };
    });
  }, []);

  const runningIds = useMemo(() => {
    const ids = new Set<string>();
    for (const [id, s] of Object.entries(streams)) {
      if (s.status.phase === "running") ids.add(id);
    }
    return ids;
  }, [streams]);

  return {
    streams,
    runningIds,
    submit,
    stop,
    reset,
    clearCompletion,
  };
}
