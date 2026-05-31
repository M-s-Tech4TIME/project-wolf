import {
  Children,
  cloneElement,
  Fragment,
  isValidElement,
  type ReactElement,
  type ReactNode,
} from "react";

/**
 * Walk a tree of React children and wrap every case-insensitive
 * substring match of `query` with a styled `<mark>` (Slice 5.0c-i.5
 * rewrite).
 *
 * Design notes:
 *
 *   - The earlier (5.0c-i.4) helper threaded a counter + activeLocalIdx
 *     to colour one specific mark in the vivid active style. That was
 *     fragile because the raw-text match enumeration in MessageThread
 *     drifted from the rendered-tree mark emission whenever markdown
 *     wrapped a match inside `<strong>`, inline `<code>`, or any other
 *     React element (the walker didn't recurse into elements). The
 *     drift meant "5 of 8 matches" but only 3 visible marks, and the
 *     orange highlight pointed at the wrong one — most visible to the
 *     user when traversing from a user message to a Wolf response.
 *
 *   - This rewrite emits ALL marks with the SAME passive class +
 *     `data-find-match="true"`. The currently-active mark is selected
 *     by MessageThread via a DOM mutation effect that picks the i-th
 *     match in document order and sets `data-find-active="true"`. CSS
 *     in globals.css colours that one mark vivid orange. Match count
 *     is read off the DOM with `querySelectorAll`, so it is by
 *     construction equal to the number of rendered marks.
 *
 *   - The walker now RECURSES into HTML React elements (`<strong>`,
 *     `<em>`, `<a>`, inline `<code>`, …) so matches inside them get
 *     highlighted. Two opt-outs:
 *       1. Function-component elements (like FencedCodeBlock) — those
 *          have their own syntax-highlighting and we don't want
 *          `<mark>`s breaking up colourised tokens.
 *       2. Elements with `data-grounding-chip="true"` — the grounding
 *          chips already carry their own background and shouldn't
 *          double-paint.
 */
export function highlightSearchInChildren(
  children: ReactNode,
  query: string,
): ReactNode {
  if (!query) return children;
  const trimmed = query.trim();
  if (!trimmed) return children;
  const needle = trimmed.toLowerCase();
  const markClass =
    "wolf-find-mark rounded-sm bg-amber-200/70 px-0.5 text-foreground dark:bg-amber-400/40";

  return Children.map(children, (child, index) => {
    if (typeof child === "string") {
      const lower = child.toLowerCase();
      if (!lower.includes(needle)) return child;
      const parts: ReactNode[] = [];
      let lastEnd = 0;
      let pos = lower.indexOf(needle);
      let chunkIdx = 0;
      while (pos !== -1) {
        if (pos > lastEnd) parts.push(child.slice(lastEnd, pos));
        parts.push(
          <mark
            key={`m-${index}-${chunkIdx++}`}
            className={markClass}
            data-find-match="true"
          >
            {child.slice(pos, pos + needle.length)}
          </mark>,
        );
        lastEnd = pos + needle.length;
        pos = lower.indexOf(needle, lastEnd);
      }
      if (lastEnd < child.length) parts.push(child.slice(lastEnd));
      return <Fragment key={`f-${index}`}>{parts}</Fragment>;
    }
    if (isValidElement(child)) {
      const el = child as ReactElement<{
        children?: ReactNode;
        "data-grounding-chip"?: string;
      }>;
      const props = el.props;
      // Opt-out 1: grounding chips carry their own background; don't
      //            double-paint.
      // Opt-out 2: function-component elements (FencedCodeBlock) own
      //            their internal rendering (syntax-highlighting); a
      //            `<mark>` inside them would break the token spans.
      if (props["data-grounding-chip"]) return child;
      if (typeof el.type !== "string") return child;
      const recursed = highlightSearchInChildren(props.children, query);
      return cloneElement(el, undefined, recursed);
    }
    return child;
  });
}
