"use client";

import { AlertTriangle, Info } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { Children, Fragment, isValidElement } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

/**
 * Two grounding markers with distinct severities (Slice 5.0b):
 *   [unverified]  — yellow "caution": a factual claim the evidence neither
 *                   confirms nor contradicts (general knowledge / inference).
 *   [unsupported] — red: a specific claim that contradicts the evidence or
 *                   fabricates specifics absent from it.
 * Order matters only for the split regex; both are matched in one pass.
 */
const GROUNDING_MARKERS = {
  "[unsupported]": {
    label: "unsupported",
    className:
      "bg-destructive/15 text-destructive",
    title:
      "Flagged by the grounding validator as UNSUPPORTED — this specific claim contradicts, or is absent from, every tool result and retrieved knowledge chunk. Treat with caution.",
    icon: AlertTriangle,
  },
  "[unverified]": {
    label: "unverified",
    className:
      "bg-amber-400/20 text-amber-700 dark:text-amber-400",
    title:
      "Flagged by the grounding validator as UNVERIFIED — the evidence neither confirms nor contradicts this. It may be correct general knowledge or inference, but Wolf could not verify it from the tools/knowledge used.",
    icon: Info,
  },
} as const;

// Splits on either marker while keeping the delimiter (capturing group).
const MARKER_SPLIT = /(\[unsupported\]|\[unverified\])/;

/**
 * Walk a React children tree, splitting any string node on the grounding
 * markers and replacing each with a styled chip (yellow caution / red).
 * Non-string nodes pass through unchanged. Working at the rendered-tree
 * level avoids a `rehype-raw` dependency for inline HTML.
 */
function highlightGroundingMarkers(children: ReactNode): ReactNode {
  return Children.map(children, (child, index) => {
    if (typeof child === "string") {
      if (!child.includes("[unverified]") && !child.includes("[unsupported]")) {
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
}: {
  children: string;
  className?: string;
}) {
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
          // Highlight grounding markers (yellow [unverified] / red
          // [unsupported]) wherever they appear in flowing text (paragraphs,
          // list items, table cells, blockquotes).
          p: ({ children }) => <p>{highlightGroundingMarkers(children)}</p>,
          li: ({ children }) => <li>{highlightGroundingMarkers(children)}</li>,
          td: ({ children }) => <td>{highlightGroundingMarkers(children)}</td>,
          th: ({ children }) => <th>{highlightGroundingMarkers(children)}</th>,
          blockquote: ({ children }) => (
            <blockquote>{highlightGroundingMarkers(children)}</blockquote>
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
        className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]"
        {...rest}
      >
        {children}
      </code>
    );
  }

  const language = className?.replace("language-", "") || "";
  return (
    <pre className="my-2 overflow-x-auto rounded-md border border-border bg-muted/50 p-3 text-xs">
      {language ? (
        <div className="-mt-1 mb-2 text-[10px] uppercase tracking-wider text-muted-foreground">
          {language}
        </div>
      ) : null}
      <code className="font-mono leading-relaxed" {...rest}>
        {children}
      </code>
    </pre>
  );
}
