"use client";

import { useCallback, useRef, useState } from "react";

import { ApiError, chatStream } from "@/lib/api";
import type {
  ChatExchange,
  Citation,
  LoopEvent,
  ToolEvent,
} from "@/lib/types";

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
  /** Submit a question; rejects only on programmer errors. */
  submit: (question: string) => Promise<void>;
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
  const startedAtRef = useRef<string>("");
  const questionRef = useRef<string>("");

  const reset = useCallback(() => {
    setStatus({ phase: "idle" });
    setExchange(null);
    setToolEvents([]);
    setCitations([]);
    setError(null);
  }, []);

  const submit = useCallback(async (question: string) => {
    const trimmed = question.trim();
    if (!trimmed) return;

    setStatus({ phase: "running", last_event_type: undefined });
    setExchange(null);
    setToolEvents([]);
    setCitations([]);
    setError(null);
    startedAtRef.current = new Date().toISOString();
    questionRef.current = trimmed;

    try {
      await chatStream({ question: trimmed }, (event) => {
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
            setStatus((s) => ({
              ...s,
              step: asNumber(event.data.step),
              last_event_type: event.type,
              message: `Step ${asNumber(event.data.step) + 1}${s.step_budget ? `/${s.step_budget}` : ""}`,
            }));
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
            setToolEvents((prev) => [...prev, tool]);
            if (tool.citation) {
              setCitations((prev) => [...prev, tool.citation as Citation]);
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
            const completed: ChatExchange = {
              id: asString(data.loop_id) || crypto.randomUUID(),
              question: questionRef.current,
              answer: asString(data.content),
              citations: (data.citations as Citation[] | undefined) ?? [],
              tool_events: toolEvents,
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
            };
            setExchange(completed);
            setCitations(completed.citations);
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
  }, [toolEvents]);

  return { status, exchange, toolEvents, citations, error, submit, reset };
}
