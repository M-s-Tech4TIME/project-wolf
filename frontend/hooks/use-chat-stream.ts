"use client";

import { useCallback, useRef, useState } from "react";

import { ApiError, chatStream } from "@/lib/api";
import type {
  ChatExchange,
  Citation,
  ConversationTurn,
  LoopEvent,
  ToolEvent,
} from "@/lib/types";
import { randomId } from "@/lib/uuid";

/**
 * State machine for a single streaming chat exchange.
 *
 *   idle ─submit→ running ─answer→ done
 *                        └─error→ error
 */
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

export type UseChatStream = {
  status: StreamStatus;
  exchange: ChatExchange | null;
  toolEvents: ToolEvent[];
  citations: Citation[];
  error: string | null;
  /** The user's question for the in-flight request (or empty when idle). */
  currentQuestion: string;
  /**
   * Token-by-token accumulator for the in-flight model response (Slice
   * 5.0c-d). Reset on every `step.started`, appended to on every
   * `model.delta`, finalised by the `answer` event.
   */
  streamingAnswer: string;
  /** Submit a question with optional prior turns; rejects only on programmer errors. */
  submit: (question: string, history?: ConversationTurn[]) => Promise<void>;
  reset: () => void;
};

function asString(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

function asNumber(v: unknown, fallback = 0): number {
  return typeof v === "number" ? v : fallback;
}

export function useChatStream(): UseChatStream {
  const [status, setStatus] = useState<StreamStatus>({ phase: "idle" });
  const [exchange, setExchange] = useState<ChatExchange | null>(null);
  const [toolEvents, setToolEvents] = useState<ToolEvent[]>([]);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [currentQuestion, setCurrentQuestion] = useState<string>("");
  const [streamingAnswer, setStreamingAnswer] = useState<string>("");
  const startedAtRef = useRef<string>("");
  const questionRef = useRef<string>("");
  // Refs mirror the React state so the "answer" event handler can read
  // the freshest values even while pending setState updates are batched.
  // Without this, the archived exchange's tool_events/citations were
  // empty on fast streams.
  const toolEventsRef = useRef<ToolEvent[]>([]);
  const citationsRef = useRef<Citation[]>([]);
  // Buffer the streaming answer in a ref too — model.delta fires every
  // few ms during a stream and batching the state updates via the ref
  // avoids re-rendering on each token (we still flush the React state
  // immediately for the visible text; the ref is the source of truth
  // for accumulation across batched setState calls).
  const streamingAnswerRef = useRef<string>("");

  const reset = useCallback(() => {
    setStatus({ phase: "idle" });
    setExchange(null);
    setToolEvents([]);
    setCitations([]);
    setError(null);
    setCurrentQuestion("");
    setStreamingAnswer("");
    toolEventsRef.current = [];
    citationsRef.current = [];
    streamingAnswerRef.current = "";
  }, []);

  const submit = useCallback(async (question: string, history: ConversationTurn[] = []) => {
    const trimmed = question.trim();
    if (!trimmed) return;

    setStatus({ phase: "running", last_event_type: undefined });
    setExchange(null);
    setToolEvents([]);
    setCitations([]);
    setError(null);
    setCurrentQuestion(trimmed);
    setStreamingAnswer("");
    toolEventsRef.current = [];
    citationsRef.current = [];
    streamingAnswerRef.current = "";
    startedAtRef.current = new Date().toISOString();
    questionRef.current = trimmed;

    try {
      await chatStream({ question: trimmed, history }, (event) => {
        switch (event.type) {
          case "loop.started": {
            setStatus((s) => ({
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
            // Each step starts a fresh model call; clear the streaming
            // buffer so the previous step's text doesn't bleed into this
            // one. If this step ends up calling tools, the buffer (which
            // may have collected a "thinking..." prefix) is discarded on
            // the next step.started; if this is the final answer step,
            // the buffer becomes the visible streamed answer.
            streamingAnswerRef.current = "";
            setStreamingAnswer("");
            setStatus((s) => ({
              ...s,
              step: asNumber(event.data.step),
              last_event_type: event.type,
              message: `Step ${asNumber(event.data.step) + 1}${s.step_budget ? `/${s.step_budget}` : ""}`,
            }));
            break;
          }
          case "model.delta": {
            const delta = asString(event.data.content_delta);
            if (delta) {
              streamingAnswerRef.current = streamingAnswerRef.current + delta;
              setStreamingAnswer(streamingAnswerRef.current);
            }
            break;
          }
          case "model.call.completed": {
            const toolCount = asNumber(event.data.tool_call_count);
            setStatus((s) => ({
              ...s,
              last_event_type: event.type,
              message:
                toolCount > 0
                  ? `Model returned ${toolCount} tool call${toolCount === 1 ? "" : "s"}`
                  : "Model drafting answer…",
            }));
            break;
          }
          case "model.call.failed": {
            setStatus((s) => ({
              ...s,
              last_event_type: event.type,
              message: `Model call failed: ${asString(event.data.detail, "unknown error")}`,
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
              counts: event.data.counts as Record<string, number> | undefined,
              error:
                typeof event.data.error === "string"
                  ? event.data.error
                  : undefined,
            };
            toolEventsRef.current = [...toolEventsRef.current, tool];
            setToolEvents(toolEventsRef.current);
            if (tool.citation) {
              citationsRef.current = [
                ...citationsRef.current,
                tool.citation as Citation,
              ];
              setCitations(citationsRef.current);
            }
            setStatus((s) => ({
              ...s,
              last_event_type: event.type,
              message: tool.success
                ? `Got ${tool.tool_name} result (${tool.elapsed_ms}ms)`
                : `${tool.tool_name} failed: ${tool.error ?? "unknown"}`,
            }));
            break;
          }
          case "answer": {
            const data = event.data;
            const completedAt = new Date().toISOString();
            // Prefer the backend's authoritative citations on the answer
            // event; fall back to what we collected from tool events if
            // the payload didn't include them.
            const backendCitations =
              (data.citations as Citation[] | undefined) ?? [];
            const finalCitations =
              backendCitations.length > 0
                ? backendCitations
                : citationsRef.current;
            const asNullableNumber = (v: unknown): number | null =>
              typeof v === "number" ? v : null;
            const completed: ChatExchange = {
              id: asString(data.loop_id) || randomId(),
              question: questionRef.current,
              answer: asString(data.content),
              citations: finalCitations,
              tool_events: toolEventsRef.current,
              stop_reason:
                (data.stop_reason as ChatExchange["stop_reason"]) ?? "answer",
              loop_id: asString(data.loop_id),
              strategy: asString(data.strategy),
              model_id: asString(data.model_id),
              step_count: asNumber(data.step_count),
              tool_call_count: asNumber(data.tool_call_count),
              input_tokens: asNumber(data.input_tokens),
              output_tokens: asNumber(data.output_tokens),
              started_at: startedAtRef.current,
              completed_at: completedAt,
              grounding_supported: asNullableNumber(data.grounding_supported),
              grounding_unsupported: asNullableNumber(data.grounding_unsupported),
              grounding_uncertain: asNullableNumber(data.grounding_uncertain),
              grounding_unverifiable: asNullableNumber(data.grounding_unverifiable),
            };
            setExchange(completed);
            citationsRef.current = finalCitations;
            setCitations(finalCitations);
            // The streaming buffer has served its purpose — the answer
            // bubble takes over rendering. Clearing here avoids a brief
            // flash of duplicate text if the in-flight view is still on
            // screen during the transition.
            streamingAnswerRef.current = "";
            setStreamingAnswer("");
            setStatus((s) => ({
              ...s,
              phase: "done",
              last_event_type: event.type,
              message: undefined,
            }));
            break;
          }
        }
      });
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.status}: ${err.message}`
          : err instanceof Error
            ? err.message
            : "Streaming failed";
      setError(msg);
      setStatus({ phase: "error", message: msg });
    }
    // submit reads from refs and module-level helpers only; React state is
    // *written* by it, not read.  Empty deps keeps the callback identity
    // stable so callers don't re-bind on every status change.
  }, []);

  return {
    status,
    exchange,
    toolEvents,
    citations,
    error,
    currentQuestion,
    streamingAnswer,
    submit,
    reset,
  };
}
