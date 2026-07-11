"use client";

import {
  AlertTriangle,
  Check,
  Copy,
  ImageIcon,
  Info,
  MessageCircle,
} from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { Children, Fragment, isValidElement, useCallback, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

import { copyText } from "@/lib/clipboard";
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
}: {
  children: string;
  className?: string;
}) {
  const decorate = (nodes: ReactNode): ReactNode =>
    highlightGroundingMarkers(nodes);
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
        // h4–h6 previously fell through unstyled (browser defaults render h5/h6
        // SMALLER than body text inside a text-sm container — deep outlines
        // looked broken). Step down deliberately instead.
        "[&_h4]:mb-1 [&_h4]:mt-2 [&_h4]:text-sm [&_h4]:font-medium",
        "[&_h5]:mb-1 [&_h5]:mt-2 [&_h5]:text-xs [&_h5]:font-semibold [&_h5]:uppercase [&_h5]:tracking-wide",
        "[&_h6]:mb-1 [&_h6]:mt-2 [&_h6]:text-xs [&_h6]:font-medium [&_h6]:uppercase [&_h6]:tracking-wide [&_h6]:text-muted-foreground",
        // GFM strikethrough reads as "retracted", not emphasized.
        "[&_del]:text-muted-foreground [&_del]:line-through",
        // GFM task lists: remark-gfm tags the list `contains-task-list` and
        // renders a checkbox per item — drop the disc so items aren't
        // double-marked (bullet + checkbox).
        "[&_ul.contains-task-list]:list-none [&_ul.contains-task-list]:pl-1",
        "[&_blockquote]:my-2 [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground",
        // Table itself fills the width; the horizontal-scroll wrapper is
        // applied by the `table` component override below so a wide table
        // scrolls inside the message instead of overflowing the bubble.
        "[&_table]:w-full [&_table]:border-collapse",
        "[&_th]:border [&_th]:border-border [&_th]:bg-muted/40 [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_th]:align-top",
        "[&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1 [&_td]:align-top",
        "[&_a]:text-primary [&_a]:underline [&_a]:underline-offset-2",
        "[&_hr]:my-3 [&_hr]:border-border",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[
          // Slice 5.0c-i.5: per-language tokenisation for fenced code
          // blocks. `detect: true` lets highlight.js auto-detect when a
          // block doesn't declare ```language. `subset: false` keeps
          // ALL bundled grammars available — Python / YAML / JSON /
          // Bash / TypeScript / SQL / etc all get coloured without us
          // hand-listing them. Token classes (.hljs-keyword,
          // .hljs-string, …) are styled by globals.css + the github
          // theme imported in layout.tsx (light + a dark override).
          [rehypeHighlight, { detect: true, subset: false }],
        ]}
        components={{
          code: CodeBlock,
          pre: ({ children }) => <>{children}</>,
          // Wide tables (many columns / long cells) get a horizontal-scroll
          // wrapper so they stay inside the message bubble instead of
          // pushing past it and breaking the conversation's alignment.
          // The parent bubble carries `min-w-0` so this scroll engages
          // rather than the flex item growing.
          table: ({ children }) => (
            <div className="my-2 max-w-full overflow-x-auto [scrollbar-width:thin] [&::-webkit-scrollbar]:h-2 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-foreground/30 hover:[&::-webkit-scrollbar-thumb]:bg-foreground/50">
              <table>{children}</table>
            </div>
          ),
          // Highlight grounding markers in flowing prose (paragraphs,
          // list items, table cells, blockquotes). Code blocks pass
          // through unchanged because the walker doesn't recurse
          // into elements.
          p: ({ children }) => <p>{decorate(children)}</p>,
          li: ({ children }) => <li>{decorate(children)}</li>,
          td: ({ children }) => <td>{decorate(children)}</td>,
          th: ({ children }) => <th>{decorate(children)}</th>,
          blockquote: ({ children }) => (
            <blockquote>{decorate(children)}</blockquote>
          ),
          // A grounding marker can land INSIDE emphasis (e.g. a bolded
          // sentence followed by its verdict) — the walker doesn't recurse
          // into elements, so these need their own decoration pass.
          strong: ({ children }) => <strong>{decorate(children)}</strong>,
          em: ({ children }) => <em>{decorate(children)}</em>,
          // External links leave the app in a NEW tab — a chat answer must
          // never navigate the analyst's conversation away. Relative/anchor
          // links (rare) keep default behaviour.
          a: ({ href, children, ...rest }) => {
            const external =
              typeof href === "string" && /^https?:\/\//i.test(href);
            return (
              <a
                href={href}
                {...(external
                  ? { target: "_blank", rel: "noopener noreferrer" }
                  : {})}
                {...rest}
              >
                {children}
              </a>
            );
          },
          // NEVER auto-fetch a remote image: answer text can quote content
          // that arrived from the open web (web_search / web_fetch), and a
          // markdown image would make every viewer's browser call an
          // attacker-chosen URL on render (a tracking/exfiltration beacon).
          // Render an explicit link the analyst can choose to open instead.
          img: ({ src, alt }) => {
            const href = typeof src === "string" ? src : "";
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex max-w-full items-center gap-1 align-baseline"
                title={href}
              >
                <ImageIcon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <span className="truncate">{alt || href || "image"}</span>
              </a>
            );
          },
          // GFM task-list checkboxes (read-only state markers).
          input: (props) =>
            props.type === "checkbox" ? (
              <input
                {...props}
                className="mr-1.5 h-3.5 w-3.5 translate-y-[2px] accent-[var(--palette-steel-blue)]"
              />
            ) : (
              <input {...props} />
            ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

// A short in-sentence reference (id, IP, path, port, status, flag) is inline
// code. Anything longer than this, with whitespace, is almost certainly a
// command or sentence the model mis-wrapped in single backticks — promote it
// to a fenced block so it renders as a scrollable code box, not a run-on pill.
const INLINE_MAX_LEN = 72;

/**
 * Model-agnostic guard: react-markdown only marks a fence (```lang) as a block;
 * a single-backtick span is always "inline". But weaker models sometimes wrap a
 * whole multi-line command sequence in single backticks (Wolf's exact reported
 * bug), which would render as one long wrapping pill that breaks the layout.
 * Detect that shape — a newline, or long content containing whitespace — and
 * treat it as a block. A genuine short reference is left as an inline pill.
 * The primary fix is the system prompt; this is the safety net that keeps the
 * UI correct regardless of which model produced the text.
 */
function looksLikeMisemittedBlock(text: string): boolean {
  return text.includes("\n") || (text.length > INLINE_MAX_LEN && /\s/.test(text));
}

function CodeBlock(props: ComponentProps<"code">) {
  const { className, children, ...rest } = props;
  // Distinguish a fenced block from inline code, and recover its language.
  //
  // rehype-highlight rewrites a fenced block's className to a SPACE-JOINED set
  // that always carries the (explicit OR auto-detected) language, e.g.
  // "hljs language-bash" / "hljs language-ini" / "hljs language-xml". The old
  // check `className.startsWith("language-")` failed because the string starts
  // with "hljs " — so every block fell through to language="" and the header
  // read a generic "CODE" even though the language was right there. Parse the
  // tokens instead: a block is anything tagged `hljs` or `language-*`, and the
  // label is the `language-*` token stripped of its prefix. Inline code has no
  // className at all. This makes the header dynamic (BASH / INI / XML / JSON …)
  // for every model, honouring an explicit ```lang fence and highlight.js's
  // detection alike.
  const classTokens = typeof className === "string" ? className.split(/\s+/) : [];
  const languageToken = classTokens.find((t) => t.startsWith("language-"));
  const language = languageToken ? languageToken.slice("language-".length) : "";
  const isBlock = languageToken !== undefined || classTokens.includes("hljs");

  if (!isBlock) {
    // Defensive promotion (see looksLikeMisemittedBlock): a run-on or
    // multi-line command wrongly wrapped in single backticks renders as a
    // proper code box instead of a layout-breaking pill.
    if (looksLikeMisemittedBlock(nodeToString(children))) {
      return (
        <FencedCodeBlock language="" codeProps={rest}>
          {children}
        </FencedCodeBlock>
      );
    }
    return (
      <code
        // Slice 5.0c-i.4: inline code text colour uses the Wolf palette's
        // Dusk Blue (#274c77) on light backgrounds for a calmer contrast
        // against `bg-muted`. Icy Blue (#a3cef1) takes over in dark mode.
        // Slice 5.0c-i.5: bold weight too — the user wants identifiers
        // and timestamps to read as visually structural, not prose.
        className="rounded bg-muted px-1 py-0.5 font-mono font-semibold text-[0.85em] text-[var(--palette-dusk-blue)] dark:text-[var(--palette-icy-blue)]"
        {...rest}
      >
        {children}
      </code>
    );
  }

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
      <pre className="overflow-x-auto p-3 text-xs [scrollbar-width:thin] [&::-webkit-scrollbar]:h-2 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-foreground/30 hover:[&::-webkit-scrollbar-thumb]:bg-foreground/50">
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
