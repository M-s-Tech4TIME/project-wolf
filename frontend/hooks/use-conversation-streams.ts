"use client";

import { useCallback, useMemo, useRef, useState } from "react";

import { phraseFor } from "@/lib/activity-phrases";
import { ApiError, chatStream } from "@/lib/api";
import type {
  ChatExchange,
  Citation,
  ConversationTurn,
  LoopEvent,
  ToolEvent,
} from "@/lib/types";
import { randomId } from "@/lib/uuid";

export type StreamPhase = "idle" | "running" | "done" | "error";

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
  exchange: ChatExchange | null;
  toolEvents: ToolEvent[];
  citations: Citation[];
  error: string | null;
  currentQuestion: string;
  streamingAnswer: string;
};

/**
 * Default state returned for any conversation the hook hasn't seen
 * yet. Exported so consumers can use it as a stable fallback when
 * reading from `streams[convoId] ?? NEUTRAL_STREAM`.
 */
export const NEUTRAL_STREAM: StreamState = {
  status: { phase: "idle" },
  exchange: null,
  toolEvents: [],
  citations: [],
  error: null,
  currentQuestion: "",
  streamingAnswer: "",
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
   *  simultaneously. */
  submit: (
    convoId: string,
    question: string,
    history?: ConversationTurn[],
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
      history: ConversationTurn[] = [],
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

      // Initialise displayable state.
      updateStream(convoId, () => ({
        status: { phase: "running", last_event_type: undefined },
        exchange: null,
        toolEvents: [],
        citations: [],
        error: null,
        currentQuestion: trimmed,
        streamingAnswer: "",
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
              case "grounding.completed": {
                const sup = asNumber(event.data.supported);
                const uncertain = asNumber(event.data.uncertain);
                const unsup = asNumber(event.data.unsupported);
                const ran = Boolean(event.data.ran);
                if (ran) {
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
                const completed: ChatExchange = {
                  id: asString(data.loop_id) || randomId(),
                  question: questionRef.current[convoId] ?? trimmed,
                  answer: asString(data.content),
                  citations: finalCitations,
                  tool_events: toolEventsRef.current[convoId] ?? [],
                  stop_reason:
                    (data.stop_reason as ChatExchange["stop_reason"]) ??
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
                };
                citationsRef.current[convoId] = finalCitations;
                streamingAnswerRef.current[convoId] = "";
                updateStream(convoId, (s) => ({
                  ...s,
                  exchange: completed,
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
          // Synthesise an interrupted ChatExchange. Token counts /
          // step counts default to 0 because we don't have the
          // backend's tallies; the tool_call_count reflects how many
          // tools the agent had actually completed before the abort.
          const interrupted: ChatExchange = {
            id: randomId(),
            question: questionRef.current[convoId] ?? trimmed,
            answer: partialAnswer,
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
          };
          streamingAnswerRef.current[convoId] = "";
          updateStream(convoId, (s) => ({
            ...s,
            exchange: interrupted,
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
    setStreams((prev) => {
      const next = { ...prev };
      delete next[convoId];
      return next;
    });
  }, []);

  const runningIds = useMemo(() => {
    const ids = new Set<string>();
    for (const [id, s] of Object.entries(streams)) {
      if (s.status.phase === "running") ids.add(id);
    }
    return ids;
  }, [streams]);

  return { streams, runningIds, submit, stop, reset };
}
