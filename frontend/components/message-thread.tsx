"use client";

import {
  ArrowDown,
  Bot,
  ChevronDown,
  ChevronUp,
  Copy,
  Loader2,
  Pencil,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  User,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAuth } from "@/components/auth-provider";
import { Markdown } from "@/components/markdown";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { UseChatStream } from "@/hooks/use-chat-stream";
import { copyText } from "@/lib/clipboard";
import { absoluteTimeTitle, relativeTime } from "@/lib/format";
import type { ChatExchange } from "@/lib/types";

type Props = {
  exchanges: ChatExchange[];
  stream: UseChatStream;
  /**
   * True only when the currently-displayed conversation is the one the
   * stream is in-flight for (Slice 5.0c-h). When false, the user has
   * navigated away from a still-running conversation — render the
   * archived exchanges only, no live view.
   */
  isActiveStreaming?: boolean;
  /** Drops the question into the composer for editing (Slice 5.0c-f). */
  onEdit?: (question: string) => void;
  /** Re-asks the question by pre-filling the composer (Slice 5.0c-f). */
  onRetry?: (question: string) => void;
  /** Quick-action card click from the greeting screen (Slice 5.0c-f). */
  onQuickAsk?: (question: string) => void;
  /**
   * Retry on a Wolf answer (Slice 5.0c-g). Re-submits the originating
   * question with retry_nudge=true so the backend asks the model to
   * critique its previous attempt. Parent is responsible for building
   * history that includes the previous Q→A pair.
   */
  onAssistantRetry?: (originatingQuestion: string) => void;
};

/**
 * Long-message fade threshold (Slice 5.0c-f). Anything past this gets
 * collapsed with a gradient and a "Show more" toggle. Chosen so two- or
 * three-line questions stay fully visible while paragraph-length context
 * dumps stay compact. Measured in characters since the message is
 * pre-wrap and proportional-font.
 */
const LONG_MESSAGE_THRESHOLD = 280;

/**
 * Renders every turn in the active conversation in order, then (if a
 * stream is in-flight) the live streaming view at the bottom.
 */
export function MessageThread({
  exchanges,
  stream,
  isActiveStreaming = false,
  onEdit,
  onRetry,
  onQuickAsk,
  onAssistantRetry,
}: Props) {
  // The live view is gated on isActiveStreaming so a stream running in
  // a *different* conversation doesn't bleed into the one the user is
  // looking at. Errors only show in the active conversation too — the
  // error state is bound to the conversation it occurred in (Slice
  // 5.0c-h).
  const showStreamView =
    isActiveStreaming &&
    (stream.status.phase === "running" || stream.status.phase === "error");
  const isRunning = isActiveStreaming && stream.status.phase === "running";
  const empty = exchanges.length === 0 && !showStreamView;

  // The chat uses a NATIVE scroll container instead of Radix's
  // ScrollArea. Radix's primitive introduces a nested viewport that
  // didn't reliably constrain inside our flex chain, so long
  // conversations were overflowing past the composer instead of
  // scrolling. Native overflow-y-auto inside a `min-h-0 flex-1`
  // parent is rock-solid.
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    bottomRef.current?.scrollIntoView({ behavior, block: "end" });
  }, []);

  useEffect(() => {
    scrollToBottom("smooth");
  }, [exchanges.length, isRunning, scrollToBottom]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      setShowScrollToBottom(distance > 200);
    };
    onScroll();
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [exchanges.length, isRunning]);

  // Edit / Retry only make semantic sense on the most recent user message;
  // applying them to an arbitrary mid-conversation message would either
  // fork the thread (out of scope) or be silently misleading. So we hand
  // those callbacks down only for the last archived exchange. Same logic
  // for assistant Retry (Slice 5.0c-g) — only the latest Wolf answer has
  // meaningful "retry this".
  const lastExchangeIdx = exchanges.length - 1;

  // Greeting screen exit animation (Slice 5.0c-g, slowed in 5.0c-i to
  // 1500ms after user feedback that the original 280ms was too snappy).
  // When the user submits their first message, `empty` flips false; we
  // keep the greeting in the DOM for the full transition cycle with
  // opacity-0 so it fades out smoothly. After the timeout the greeting
  // unmounts entirely.
  const [renderGreeting, setRenderGreeting] = useState(empty);
  useEffect(() => {
    if (empty) {
      // Re-mounting the greeting after the user clears + starts a new
      // chat is a real path; setting state from the effect here is the
      // right shape — the SSR pass starts from `empty` so this only
      // fires when the value actually flips back to true.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setRenderGreeting(true);
      return;
    }
    const t = window.setTimeout(() => setRenderGreeting(false), 1500);
    return () => window.clearTimeout(t);
  }, [empty]);

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto overflow-x-hidden [scrollbar-gutter:stable] [&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-foreground/30 hover:[&::-webkit-scrollbar-thumb]:bg-foreground/50"
      >
        <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
          {renderGreeting ? (
            <div
              className={`transition-opacity duration-[1500ms] ease-out ${
                empty ? "opacity-100" : "pointer-events-none opacity-0"
              }`}
              aria-hidden={empty ? undefined : "true"}
            >
              <GreetingScreen onQuickAsk={onQuickAsk} />
            </div>
          ) : null}

          {exchanges.map((ex, i) => {
            const isLast = i === lastExchangeIdx;
            return (
              <CompletedExchange
                key={ex.id}
                exchange={ex}
                showMeta
                onEdit={isLast ? onEdit : undefined}
                onRetry={isLast ? onRetry : undefined}
                onAssistantRetry={isLast ? onAssistantRetry : undefined}
              />
            );
          })}

          {showStreamView ? (
            /* Slice 5.0c-i: the in-flight chat view fades + slides in
               instead of snapping. tw-animate-css's `animate-in
               fade-in-0 slide-in-from-bottom-2` fires once on mount of
               this container. Duration matches the greeting fade-out so
               the two crossfade naturally. */
            <div className="space-y-6 animate-in fade-in-0 slide-in-from-bottom-2 duration-[700ms] ease-out">
              {stream.currentQuestion ? (
                <UserBubble
                  text={stream.currentQuestion}
                  timestamp={null}
                />
              ) : null}
              <StreamingView stream={stream} />
            </div>
          ) : null}

          <div ref={bottomRef} />
        </div>
      </div>
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

// ─── Greeting screen (Slice 5.0c-f piece 5) ──────────────────────────────────

type QuickAction = {
  title: string;
  blurb: string;
  prompt: string;
};

const QUICK_ACTIONS: QuickAction[] = [
  {
    title: "Recent critical alerts",
    blurb: "Wazuh hits at rule level ≥ 12 in the last 24 h.",
    prompt:
      "What are the most critical Wazuh alerts (rule level >= 12) in the last 24 hours? Summarise by rule and agent.",
  },
  {
    title: "Suspicious authentication",
    blurb: "Failed logins, brute-force patterns, off-hours sign-ins.",
    prompt:
      "Show me suspicious authentication activity in the last 24 hours — failed logins, brute-force patterns, off-hours sign-ins.",
  },
  {
    title: "Agent health",
    blurb: "Which agents stopped reporting and when.",
    prompt:
      "Which Wazuh agents are currently disconnected or have stopped reporting? Include when they last checked in.",
  },
  {
    title: "MITRE technique lookup",
    blurb: "Explain a T-code and check if we've seen it.",
    prompt:
      "Explain MITRE ATT&CK technique T1059 (Command and Scripting Interpreter) and check whether we've seen related alerts in the last 7 days.",
  },
];

function GreetingScreen({ onQuickAsk }: { onQuickAsk?: (q: string) => void }) {
  const { me } = useAuth();
  // Greeting is rendered after mount so the server vs client time-of-day
  // can never disagree. Until the effect runs we show a neutral fallback.
  const [hour, setHour] = useState<number | null>(null);
  useEffect(() => {
    // Deliberately deferred to the client so SSR and client agree on the
    // initial render — Date().getHours() would mismatch and hydrate-warn.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setHour(new Date().getHours());
  }, []);

  const greeting = useMemo(() => {
    if (hour === null) return "Welcome back";
    if (hour < 5) return "Working late";
    if (hour < 12) return "Good morning";
    if (hour < 17) return "Good afternoon";
    if (hour < 22) return "Good evening";
    return "Working late";
  }, [hour]);

  const firstName = useMemo(() => {
    const dn = me?.display_name?.trim();
    if (dn) return dn.split(/\s+/)[0];
    const local = me?.email?.split("@")[0] ?? "";
    return local || "";
  }, [me?.display_name, me?.email]);

  return (
    <div className="py-12">
      <div className="mb-8 flex flex-col items-center text-center">
        <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
          <Bot className="h-7 w-7 text-primary" />
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">
          {greeting}
          {firstName ? `, ${firstName}` : ""}.
        </h1>
        <p className="mt-2 max-w-md text-sm text-muted-foreground">
          Ask Wolf about your Wazuh deployment. Every answer is grounded in
          read-only tool calls and cited evidence.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {QUICK_ACTIONS.map((qa) => (
          <button
            key={qa.title}
            type="button"
            onClick={() => onQuickAsk?.(qa.prompt)}
            disabled={!onQuickAsk}
            className="group flex flex-col items-start gap-1 rounded-lg border border-border bg-card px-3 py-3 text-left transition-colors hover:border-primary/40 hover:bg-accent/40 disabled:opacity-60"
          >
            <span className="text-sm font-medium group-hover:text-primary">
              {qa.title}
            </span>
            <span className="text-xs text-muted-foreground">{qa.blurb}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// ─── Completed exchange + bubbles (Slice 5.0c-f pieces 3 + 4) ────────────────

function CompletedExchange({
  exchange,
  showMeta,
  onEdit,
  onRetry,
  onAssistantRetry,
}: {
  exchange: ChatExchange;
  showMeta: boolean;
  onEdit?: (question: string) => void;
  onRetry?: (question: string) => void;
  onAssistantRetry?: (originatingQuestion: string) => void;
}) {
  return (
    <div className="space-y-3">
      <UserBubble
        text={exchange.question}
        timestamp={exchange.started_at}
        onEdit={onEdit ? () => onEdit(exchange.question) : undefined}
        onRetry={onRetry ? () => onRetry(exchange.question) : undefined}
      />
      <AssistantBubble
        answer={exchange.answer}
        timestamp={exchange.completed_at}
        onRetry={
          onAssistantRetry
            ? () => onAssistantRetry(exchange.question)
            : undefined
        }
      />
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
  const hasStreamingText = stream.streamingAnswer.trim().length > 0;
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
        {hasStreamingText ? (
          /* Progressive answer rendering (Slice 5.0c-d). Shapes match the
             archived AssistantBubble so the transition to the final
             answer event doesn't shift the layout. A soft pulsing caret
             at the end hints "still generating" without being noisy. */
          <div className="rounded-lg border border-border bg-card px-4 py-3">
            <Markdown>{stream.streamingAnswer}</Markdown>
            {stream.status.phase === "running" ? (
              <span
                aria-hidden="true"
                className="ml-0.5 inline-block h-3.5 w-px animate-pulse bg-primary align-baseline"
              />
            ) : null}
          </div>
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

/**
 * User-side message bubble. Long text collapses with a fade + Show more
 * (Slice 5.0c-f). When `onEdit` / `onRetry` are provided (only for the
 * latest user message — see CompletedExchange), the hover action bar
 * shows those buttons; Copy + timestamp are always present.
 */
function UserBubble({
  text,
  timestamp,
  onEdit,
  onRetry,
}: {
  text: string;
  timestamp: string | null;
  onEdit?: () => void;
  onRetry?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > LONG_MESSAGE_THRESHOLD;
  const showExpander = isLong;
  const collapsed = isLong && !expanded;

  return (
    <div className="group flex flex-col items-end gap-1">
      <div className="flex justify-end gap-3">
        <div className="relative max-w-xl rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground">
          <div
            className={
              collapsed
                ? "relative max-h-32 overflow-hidden whitespace-pre-wrap"
                : "whitespace-pre-wrap"
            }
          >
            {text}
            {collapsed ? (
              <span
                aria-hidden="true"
                className="pointer-events-none absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t from-primary to-transparent"
              />
            ) : null}
          </div>
          {showExpander ? (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="mt-1 inline-flex items-center gap-1 text-[11px] font-medium text-primary-foreground/80 hover:text-primary-foreground hover:underline"
            >
              {expanded ? (
                <>
                  <ChevronUp className="h-3 w-3" />
                  Show less
                </>
              ) : (
                <>
                  <ChevronDown className="h-3 w-3" />
                  Show more
                </>
              )}
            </button>
          ) : null}
        </div>
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted">
          <User className="h-4 w-4" />
        </div>
      </div>
      <HoverActionBar
        align="end"
        timestamp={timestamp}
        copyText={text}
        onEdit={onEdit}
        onRetry={onRetry}
      />
    </div>
  );
}

function AssistantBubble({
  answer,
  timestamp,
  onRetry,
}: {
  answer: string;
  timestamp: string | null;
  onRetry?: () => void;
}) {
  return (
    <div className="group flex flex-col gap-1">
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
      <HoverActionBar
        align="start"
        timestamp={timestamp}
        copyText={answer}
        onRetry={onRetry}
        indent="left"
      />
    </div>
  );
}

/**
 * Per-message action chip row, revealed on hover of the surrounding
 * `.group` container (Slice 5.0c-f). All actions are progressive: copy
 * works even on stale messages, edit/retry only attach to the latest
 * user message, the date is a relative-time chip with an absolute-time
 * tooltip.
 */
function HoverActionBar({
  align,
  timestamp,
  copyText: textToCopy,
  onEdit,
  onRetry,
  indent,
}: {
  align: "start" | "end";
  timestamp: string | null;
  copyText: string;
  onEdit?: () => void;
  onRetry?: () => void;
  /** Assistant bubbles need a left-indent matching the avatar gutter. */
  indent?: "left";
}) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(async () => {
    const ok = await copyText(textToCopy);
    if (!ok) return;
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }, [textToCopy]);

  // If there's literally nothing to show, render nothing so we don't
  // reserve vertical space for an empty chrome row.
  const hasTimestamp = !!timestamp;
  const hasCopy = textToCopy.length > 0;
  if (!hasTimestamp && !hasCopy && !onEdit && !onRetry) return null;

  return (
    <div
      className={
        "flex items-center gap-1 opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-within:opacity-100 " +
        (align === "end" ? "justify-end pr-12" : "") +
        (indent === "left" ? " pl-11" : "")
      }
    >
      {hasTimestamp ? (
        <span
          className="text-[10px] text-muted-foreground"
          title={absoluteTimeTitle(timestamp!)}
        >
          {relativeTime(timestamp!)}
        </span>
      ) : null}
      {hasCopy ? (
        <ActionChip
          label={copied ? "Copied" : "Copy"}
          onClick={handleCopy}
          icon={<Copy className="h-3 w-3" />}
        />
      ) : null}
      {onRetry ? (
        <ActionChip
          label="Retry"
          onClick={onRetry}
          icon={<RotateCcw className="h-3 w-3" />}
        />
      ) : null}
      {onEdit ? (
        <ActionChip
          label="Edit"
          onClick={onEdit}
          icon={<Pencil className="h-3 w-3" />}
        />
      ) : null}
    </div>
  );
}

function ActionChip({
  label,
  onClick,
  icon,
}: {
  label: string;
  onClick: () => void;
  icon: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-accent hover:text-foreground"
    >
      {icon}
      {label}
    </button>
  );
}

