"use client";

import { Bot, Loader2, ShieldAlert, ShieldCheck, User } from "lucide-react";
import { useEffect, useRef } from "react";

import { Markdown } from "@/components/markdown";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { UseChatStream } from "@/hooks/use-chat-stream";
import type { ChatExchange } from "@/lib/types";

type Props = {
  exchanges: ChatExchange[];
  stream: UseChatStream;
};

/**
 * Renders every turn in the active conversation in order, then (if a
 * stream is in-flight) the live streaming view at the bottom.
 */
export function MessageThread({ exchanges, stream }: Props) {
  const isRunning = stream.status.phase === "running";
  const showStreamView = isRunning || stream.status.phase === "error";
  const empty = exchanges.length === 0 && !showStreamView;

  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [exchanges.length, isRunning]);

  return (
    <ScrollArea className="flex-1">
      <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
        {empty ? <EmptyState /> : null}

        {exchanges.map((ex, idx) => (
          <CompletedExchange
            key={ex.id}
            exchange={ex}
            showMeta={idx === exchanges.length - 1 && !showStreamView}
          />
        ))}

        {showStreamView ? (
          <>
            {stream.currentQuestion ? (
              <UserBubble text={stream.currentQuestion} />
            ) : null}
            <StreamingView stream={stream} />
          </>
        ) : null}

        <div ref={bottomRef} />
      </div>
    </ScrollArea>
  );
}

function EmptyState() {
  return (
    <div className="py-16 text-center text-sm text-muted-foreground">
      <Bot className="mx-auto mb-3 h-8 w-8 opacity-50" />
      <p>Start an investigation by asking a question.</p>
      <p className="mt-1 text-xs">
        Wolf will use read-only tools against your Wazuh deployment and cite every source.
      </p>
    </div>
  );
}

function CompletedExchange({
  exchange,
  showMeta,
}: {
  exchange: ChatExchange;
  showMeta: boolean;
}) {
  return (
    <div className="space-y-3">
      <UserBubble text={exchange.question} />
      <AssistantBubble answer={exchange.answer} />
      {showMeta ? (
        <div className="flex flex-wrap items-center gap-2 px-12 text-[10px] text-muted-foreground">
          <Badge variant="secondary">{exchange.strategy}</Badge>
          <Badge variant="outline">{exchange.model_id}</Badge>
          <span>·</span>
          <span>{exchange.step_count} steps</span>
          <span>·</span>
          <span>{exchange.tool_call_count} tool calls</span>
          <span>·</span>
          <span>{exchange.input_tokens + exchange.output_tokens} tokens</span>
          <GroundingBadge exchange={exchange} />
          {exchange.stop_reason !== "answer" ? (
            <Badge variant="destructive">{exchange.stop_reason}</Badge>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Per doc 06 §Hallucinated grounding: surface the validator's per-answer
 * verdict to the analyst. Renders nothing when the validator didn't run
 * (no citations / judge failed) — counts are all `null` in that case.
 */
function GroundingBadge({ exchange }: { exchange: ChatExchange }) {
  const {
    grounding_supported,
    grounding_unsupported,
    grounding_uncertain,
    grounding_unverifiable,
  } = exchange;
  if (
    grounding_supported === null &&
    grounding_unsupported === null &&
    grounding_uncertain === null &&
    grounding_unverifiable === null
  ) {
    return null;
  }
  const supported = grounding_supported ?? 0;
  const unsupported = grounding_unsupported ?? 0;
  const uncertain = grounding_uncertain ?? 0;
  const unverifiable = grounding_unverifiable ?? 0;
  const hasUnsupported = unsupported > 0;
  const hasUncertain = uncertain > 0;
  // Severity ladder: red (unsupported) > amber (uncertain) > green (clean).
  const Icon = hasUnsupported || hasUncertain ? ShieldAlert : ShieldCheck;
  const variant = hasUnsupported ? "destructive" : "outline";
  const amber =
    !hasUnsupported && hasUncertain
      ? "border-amber-400/50 bg-amber-400/15 text-amber-700 dark:text-amber-400"
      : "";
  return (
    <>
      <span>·</span>
      <Badge
        variant={variant}
        className={`gap-1 ${amber}`}
        title={
          `Grounding validator: ${supported} supported, ` +
          `${uncertain} unverified (caution), ${unsupported} unsupported, ` +
          `${unverifiable} non-factual. ` +
          (hasUnsupported
            ? "Red [unsupported] markers flag claims that contradict or are absent from the evidence."
            : hasUncertain
              ? "Yellow [unverified] markers flag claims Wolf could not verify from the evidence used."
              : "All factual claims trace back to a tool result or retrieved chunk.")
        }
      >
        <Icon className="h-3 w-3" />
        grounding {supported}✓ {uncertain}⚠ {unsupported}✗
      </Badge>
    </>
  );
}

function StreamingView({ stream }: { stream: UseChatStream }) {
  return (
    <div className="flex gap-3 px-2">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10">
        {stream.status.phase === "running" ? (
          <Loader2 className="h-4 w-4 animate-spin text-primary" />
        ) : (
          <Bot className="h-4 w-4 text-primary" />
        )}
      </div>
      <div className="flex-1 space-y-3">
        <div className="text-sm text-muted-foreground">
          {stream.status.message ?? "Working…"}
        </div>
        {stream.toolEvents.length > 0 ? (
          <ul className="space-y-1.5 text-xs">
            {stream.toolEvents.map((te) => (
              <li
                key={te.tool_call_id}
                className="flex flex-wrap items-center gap-2 rounded border border-border bg-card px-2 py-1.5"
              >
                <Badge variant={te.success ? "secondary" : "destructive"}>
                  {te.tool_name}
                </Badge>
                <span className="text-muted-foreground">
                  {te.elapsed_ms}ms
                </span>
                {te.counts ? (
                  <span className="text-muted-foreground">
                    {Object.entries(te.counts)
                      .map(([k, v]) => `${k}=${v}`)
                      .join(" · ")}
                  </span>
                ) : null}
                {te.error ? (
                  <span className="break-all text-destructive">{te.error}</span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
        {stream.status.phase === "error" && stream.error ? (
          <div className="rounded border border-destructive bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {stream.error}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end gap-3">
      <div className="max-w-xl whitespace-pre-wrap rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground">
        {text}
      </div>
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted">
        <User className="h-4 w-4" />
      </div>
    </div>
  );
}

function AssistantBubble({ answer }: { answer: string }) {
  return (
    <div className="flex gap-3">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10">
        <Bot className="h-4 w-4 text-primary" />
      </div>
      <div className="flex-1 rounded-lg border border-border bg-card px-4 py-3">
        {answer ? (
          <Markdown>{answer}</Markdown>
        ) : (
          <div className="text-sm text-muted-foreground">(empty)</div>
        )}
      </div>
    </div>
  );
}
