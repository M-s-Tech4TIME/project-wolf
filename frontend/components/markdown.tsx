"use client";

import type { ComponentProps } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

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
