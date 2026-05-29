"use client";

import { ArrowDown, Bot, Loader2, ShieldAlert, ShieldCheck, User } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Markdown } from "@/components/markdown";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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

  // Auto-scroll to the bottom on new content. The Radix Viewport is the
  // actual scrollable element; we grab it once via data-attribute so we
  // can also observe scroll position for the "scroll to bottom" button.
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    bottomRef.current?.scrollIntoView({ behavior, block: "end" });
  }, []);

  useEffect(() => {
    scrollToBottom("smooth");
  }, [exchanges.length, isRunning, scrollToBottom]);

  // Watch scroll position on the Radix Viewport so the scroll-to-bottom
  // arrow only appears when the user is significantly above the bottom.
  useEffect(() => {
    const viewport = containerRef.current?.querySelector<HTMLElement>(
      "[data-slot='scroll-area-viewport']",
    );
    if (!viewport) return;
    const onScroll = () => {
      const distance =
        viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
      setShowScrollToBottom(distance > 200);
    };
    onScroll();
    viewport.addEventListener("scroll", onScroll, { passive: true });
    return () => viewport.removeEventListener("scroll", onScroll);
  }, [exchanges.length, isRunning]);

  return (
    <div ref={containerRef} className="relative flex min-h-0 flex-1 flex-col">
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
      {showScrollToBottom ? (
        <Button
          type="button"
          size="sm"
          variant="secondary"
          onClick={() => scrollToBottom("smooth")}
          aria-label="Scroll to bottom of conversation"
          title="Scroll to bottom"
          className="absolute bottom-4 left-1/2 z-10 h-9 w-9 -translate-x-1/2 rounded-full p-0 shadow-md ring-1 ring-border"
        >
          <ArrowDown className="h-4 w-4" />
        </Button>
      ) : null}
    </div>
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
  const hasSupported = supported > 0;
  // Severity-dominant colour ladder so the worst signal wins attention,
  // with positive feedback (green) when every factual claim is Verified
  // — not just a neutral outline. Mixed counts are still communicated
  // by the {sup}✓ {unc}⚠ {unsup}✗ numbers below.
  //   🔴 red   — any Not Verified claim
  //   🟡 amber — has Uncertain claims but nothing contradicted
  //   🟢 green — every checkable claim is Verified
  //   ⚪ outline — no factual claims to ground (only preamble)
  const Icon = hasUnsupported || hasUncertain ? ShieldAlert : ShieldCheck;
  let variant: "destructive" | "outline" = "outline";
  let tintClass = "";
  if (hasUnsupported) {
    variant = "destructive";
  } else if (hasUncertain) {
    tintClass =
      "border-amber-400/50 bg-amber-400/15 text-amber-700 dark:text-amber-400";
  } else if (hasSupported) {
    tintClass =
      "border-emerald-500/50 bg-emerald-500/15 text-emerald-700 dark:text-emerald-400";
  }
  return (
    <>
      <span>·</span>
      <Badge
        variant={variant}
        className={`gap-1 ${tintClass}`}
        title={
          `Grounding validator: ${supported} Verified · ` +
          `${uncertain} Uncertain · ${unsupported} Not Verified · ` +
          `${unverifiable} Non-factual. ` +
          (hasUnsupported
            ? "Red Not Verified chips flag claims that contradict or are absent from the evidence."
            : hasUncertain
              ? "Yellow Uncertain chips flag claims Wolf could not verify from the evidence used."
              : hasSupported
                ? "All factual claims trace back to a tool result or retrieved chunk."
                : "No factual claims to verify in this answer.")
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
