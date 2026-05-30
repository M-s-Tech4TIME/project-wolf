"use client";

import { AlertTriangle, Check, Copy, Info, MessageCircle } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { Children, Fragment, isValidElement, useCallback, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { copyText } from "@/lib/clipboard";
import { highlightSearchInChildren } from "@/lib/search-highlight";
import { cn } from "@/lib/utils";

/**
 * Four grounding markers, one per verdict (Slice 5.0c-a):
 *   [verified]    — green: directly backed by tool result / knowledge chunk.
 *   [unverified]  — amber "Uncertain": factual but evidence neither confirms
 *                   nor contradicts (general knowledge / inference).
 *   [unsupported] — red "Not Verified": contradicts evidence or fabricates
 *                   specifics absent from it.
 *   [non-factual] — muted yellow: no factual content to check (preamble,
 *                   transition, opinion, instruction).
 * Distinguished from each other by a unique icon AND a unique colour
 * shade — the two yellows (Uncertain amber vs Non-factual muted) carry
 * different icons (Info vs MessageCircle) so they're not confusable at
 * a glance.
 */
const GROUNDING_MARKERS = {
  "[verified]": {
    label: "Verified",
    className: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
    title:
      "Verified by the grounding judge — this claim is directly backed by a tool result or retrieved knowledge chunk.",
    icon: Check,
  },
  "[unverified]": {
    label: "Uncertain",
    className: "bg-amber-400/20 text-amber-700 dark:text-amber-400",
    title:
      "Marked Uncertain by the grounding judge — the evidence neither confirms nor contradicts this. It may be correct general knowledge or a reasonable inference, but Wolf could not verify it from the tools/knowledge used.",
    icon: Info,
  },
  "[unsupported]": {
    label: "Not Verified",
    className: "bg-destructive/15 text-destructive",
    title:
      "Flagged Not Verified by the grounding judge — this specific claim contradicts, or is absent from, every tool result and retrieved knowledge chunk. Treat with caution.",
    icon: AlertTriangle,
  },
  "[non-factual]": {
    label: "Non-factual",
    className:
      "border border-yellow-300/50 bg-yellow-200/30 text-yellow-800 dark:text-yellow-300",
    title:
      "Non-factual — preamble, transition, instruction, or opinion. No factual content to check against the evidence.",
    icon: MessageCircle,
  },
} as const;

// Splits on any of the four markers while keeping the delimiter
// (capturing group). Order matters: longer literals first so [non-factual]
// isn't half-matched by something else.
const MARKER_SPLIT =
  /(\[non-factual\]|\[unsupported\]|\[unverified\]|\[verified\])/;

/**
 * Walk a React children tree, splitting any string node on the grounding
 * markers and replacing each with a styled chip (yellow caution / red).
 * Non-string nodes pass through unchanged. Working at the rendered-tree
 * level avoids a `rehype-raw` dependency for inline HTML.
 */
function highlightGroundingMarkers(children: ReactNode): ReactNode {
  return Children.map(children, (child, index) => {
    if (typeof child === "string") {
      if (
        !child.includes("[verified]") &&
        !child.includes("[unverified]") &&
        !child.includes("[unsupported]") &&
        !child.includes("[non-factual]")
      ) {
        return child;
      }
      const parts = child.split(MARKER_SPLIT);
      const out: ReactNode[] = parts.map((part, i) => {
        const marker = GROUNDING_MARKERS[part as keyof typeof GROUNDING_MARKERS];
        if (marker) {
          const Icon = marker.icon;
          return (
            <span
              key={`marker-${index}-${i}`}
              className={cn(
                "ml-0.5 mr-0.5 inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 align-baseline font-mono text-[0.75em] font-semibold",
                marker.className,
              )}
              title={marker.title}
            >
              <Icon className="h-3 w-3" aria-hidden="true" />
              {marker.label}
            </span>
          );
        }
        return part ? <Fragment key={`txt-${index}-${i}`}>{part}</Fragment> : null;
      });
      return <Fragment key={`fragment-${index}`}>{out}</Fragment>;
    }
    if (isValidElement(child)) return child;
    return child;
  });
}

/**
 * Render assistant answers as GitHub-flavoured markdown with Tailwind
 * styles. Code blocks get a slightly fancier treatment so the model can
 * present commands, JSON, and other structured snippets cleanly without
 * a heavyweight syntax-highlighting dependency.
 */
export function Markdown({
  children,
  className,
  searchHighlight = "",
  searchHighlightActiveLocalIdx = -1,
}: {
  children: string;
  className?: string;
  /** Slice 5.0c-i.3: when set, every case-insensitive substring match
   *  in the rendered text gets wrapped in a styled `<mark>` so the
   *  in-conversation Find result is visible inline (not just at the
   *  bubble border). Composed after the grounding-marker pass so
   *  chip JSX is preserved verbatim — only flowing prose is split. */
  searchHighlight?: string;
  /** Slice 5.0c-i.4: the LOCAL index (within this bubble's matches,
   *  0-based across every block-renderer call below) of the active
   *  Find target, or -1 if the active match is in a different bubble.
   *  Replaces the previous boolean `searchHighlightActive` so we can
   *  highlight ONE specific mark rather than the whole bubble. */
  searchHighlightActiveLocalIdx?: number;
}) {
  // Per-render counter shared by every decorate() call below so the
  // multiple per-block invocations (p / li / td / th / blockquote)
  // agree on "this is mark #N in this bubble". Fresh per render —
  // strict-mode double-renders rebuild the closure, no state bleed.
  const counter = { current: 0 };
  // Compose the two highlighters: grounding markers first (so their
  // chip JSX is treated as opaque elements by the search pass), then
  // search-query highlighting on whatever string children remain.
  const decorate = (nodes: ReactNode): ReactNode =>
    highlightSearchInChildren(
      highlightGroundingMarkers(nodes),
      searchHighlight,
      searchHighlightActiveLocalIdx,
      counter,
    );
  return (
    <div
      className={cn(
        "text-sm leading-relaxed",
        "[&_p]:my-2 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0",
        "[&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5",
        "[&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5",
        "[&_li]:my-0.5",
        "[&_strong]:font-semibold",
        "[&_em]:italic",
        "[&_h1]:mb-2 [&_h1]:mt-3 [&_h1]:text-base [&_h1]:font-semibold",
        "[&_h2]:mb-2 [&_h2]:mt-3 [&_h2]:text-sm [&_h2]:font-semibold",
        "[&_h3]:mb-1 [&_h3]:mt-2 [&_h3]:text-sm [&_h3]:font-semibold",
        "[&_blockquote]:my-2 [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground",
        "[&_table]:my-2 [&_table]:w-full [&_table]:border-collapse",
        "[&_th]:border [&_th]:border-border [&_th]:bg-muted/40 [&_th]:px-2 [&_th]:py-1 [&_th]:text-left",
        "[&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1",
        "[&_a]:text-primary [&_a]:underline [&_a]:underline-offset-2",
        "[&_hr]:my-3 [&_hr]:border-border",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code: CodeBlock,
          pre: ({ children }) => <>{children}</>,
          // Highlight grounding markers + (when set) in-conversation
          // Find query, in flowing prose (paragraphs, list items, table
          // cells, blockquotes). Code blocks and chip JSX pass through
          // unchanged because the walker doesn't recurse into elements.
          p: ({ children }) => <p>{decorate(children)}</p>,
          li: ({ children }) => <li>{decorate(children)}</li>,
          td: ({ children }) => <td>{decorate(children)}</td>,
          th: ({ children }) => <th>{decorate(children)}</th>,
          blockquote: ({ children }) => (
            <blockquote>{decorate(children)}</blockquote>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

function CodeBlock(props: ComponentProps<"code">) {
  const { className, children, ...rest } = props;
  // react-markdown gives fenced blocks a className="language-xxx" and
  // inline code no className.  Use that to distinguish.
  const isBlock = typeof className === "string" && className.startsWith("language-");

  if (!isBlock) {
    return (
      <code
        // Slice 5.0c-i.4: inline code text colour uses the Wolf palette's
        // Dusk Blue (#274c77) on light backgrounds for a calmer contrast
        // against `bg-muted`. Icy Blue (#a3cef1) takes over in dark mode
        // so the same identifier stays legible without changing the
        // background. Background tint preserved verbatim.
        className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em] text-[var(--palette-dusk-blue)] dark:text-[var(--palette-icy-blue)]"
        {...rest}
      >
        {children}
      </code>
    );
  }

  const language = className?.replace("language-", "") || "";
  return (
    <FencedCodeBlock language={language} codeProps={rest}>
      {children}
    </FencedCodeBlock>
  );
}

/**
 * Fenced code block with a header (language label) and a copy-to-clipboard
 * button that's always present and switches to a check-mark for 1.5 s on
 * successful copy. The pre/code chrome is kept small (text-xs, p-3) so it
 * doesn't dominate analyst-style answers.
 */
function FencedCodeBlock({
  language,
  codeProps,
  children,
}: {
  language: string;
  codeProps: Omit<ComponentProps<"code">, "className" | "children">;
  children: ReactNode;
}) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(async () => {
    // children may include strings and elements; collect the visible text.
    const text = nodeToString(children);
    const ok = await copyText(text);
    if (ok) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }
  }, [children]);

  return (
    <div className="group relative my-2 overflow-hidden rounded-md border border-border bg-muted/50">
      <div className="flex items-center justify-between border-b border-border/60 bg-muted/40 px-3 py-1">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {language || "code"}
        </span>
        <button
          type="button"
          onClick={onCopy}
          aria-label={copied ? "Copied" : "Copy code"}
          title={copied ? "Copied" : "Copy code"}
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
        >
          {copied ? (
            <>
              <Check className="h-3 w-3" />
              Copied
            </>
          ) : (
            <>
              <Copy className="h-3 w-3" />
              Copy
            </>
          )}
        </button>
      </div>
      <pre className="overflow-x-auto p-3 text-xs">
        <code className="font-mono leading-relaxed" {...codeProps}>
          {children}
        </code>
      </pre>
    </div>
  );
}

/** Recursively flatten React children to a plain string for clipboard. */
function nodeToString(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeToString).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) {
    return nodeToString(node.props.children ?? "");
  }
  return "";
}
