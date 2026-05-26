"use client";

import { AlertTriangle } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { Children, Fragment, isValidElement } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

const UNVERIFIED_MARKER = "[unverified]";

/**
 * Walk a React children tree, splitting any string node on the literal
 * `[unverified]` marker emitted by the Phase-3 grounding validator and
 * replacing each occurrence with a styled `<span>` carrying a hover
 * tooltip. Non-string nodes (other elements, expressions, fragments)
 * are passed through unchanged.
 *
 * This is the cleanest way to highlight inline markers without taking a
 * `rehype-raw` dependency to enable raw HTML in markdown — we work at
 * the rendered-tree level instead.
 */
function highlightUnverifiedMarkers(children: ReactNode): ReactNode {
  return Children.map(children, (child, index) => {
    if (typeof child === "string") {
      if (!child.includes(UNVERIFIED_MARKER)) return child;
      const segments = child.split(UNVERIFIED_MARKER);
      const out: ReactNode[] = [];
      segments.forEach((segment, i) => {
        if (segment) out.push(segment);
        if (i < segments.length - 1) {
          out.push(
            <span
              key={`unverified-${index}-${i}`}
              className="ml-0.5 mr-0.5 inline-flex items-center gap-0.5 rounded bg-destructive/15 px-1.5 py-0.5 align-baseline font-mono text-[0.75em] font-semibold text-destructive"
              title="This claim was flagged by the grounding validator as not supported by any tool result or retrieved knowledge chunk."
            >
              <AlertTriangle className="h-3 w-3" aria-hidden="true" />
              unverified
            </span>,
          );
        }
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
          // Highlight [unverified] markers from the grounding validator
          // wherever they appear in flowing text (paragraphs, list items,
          // table cells, blockquotes).
          p: ({ children }) => <p>{highlightUnverifiedMarkers(children)}</p>,
          li: ({ children }) => <li>{highlightUnverifiedMarkers(children)}</li>,
          td: ({ children }) => <td>{highlightUnverifiedMarkers(children)}</td>,
          th: ({ children }) => <th>{highlightUnverifiedMarkers(children)}</th>,
          blockquote: ({ children }) => (
            <blockquote>{highlightUnverifiedMarkers(children)}</blockquote>
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
