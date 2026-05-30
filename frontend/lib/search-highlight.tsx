import { Children, Fragment, isValidElement, type ReactNode } from "react";

/**
 * Walk a tree of React children and wrap every case-insensitive
 * substring match of `query` with a styled `<mark>` (Slice 5.0c-i.3).
 *
 * - Empty / whitespace `query` returns the input unchanged.
 * - Only string nodes are split; React elements are passed through.
 *   The walk does NOT recurse into element children — react-markdown's
 *   custom renderers (`p`, `li`, etc.) call this helper individually
 *   for each block, which keeps the recursion shape predictable and
 *   avoids accidentally wrapping `<mark>` inside a `<code>` or chip.
 * - `isActiveMatch` selects between two highlight shades so the user
 *   can see at a glance which exchange the in-conversation Find
 *   cursor is sitting on (matches Firefox / Chrome convention: every
 *   match in soft yellow, the active one in vivid orange).
 *
 * The match comparison preserves the original casing in the rendered
 * output — we lowercase only for the search position.
 */
export function highlightSearchInChildren(
  children: ReactNode,
  query: string,
  isActiveMatch: boolean,
): ReactNode {
  if (!query) return children;
  const trimmed = query.trim();
  if (!trimmed) return children;
  const needle = trimmed.toLowerCase();

  const activeClass =
    "rounded-sm bg-orange-400/80 px-0.5 text-foreground dark:bg-orange-500/60";
  const passiveClass =
    "rounded-sm bg-amber-200/70 px-0.5 text-foreground dark:bg-amber-400/40";
  const className = isActiveMatch ? activeClass : passiveClass;

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
            className={className}
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
    if (isValidElement(child)) return child;
    return child;
  });
}
