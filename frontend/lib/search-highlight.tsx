import {
  Children,
  cloneElement,
  Fragment,
  isValidElement,
  type ReactElement,
  type ReactNode,
} from "react";

/**
 * Mutable counter threaded through `highlightSearchInChildren` so each
 * call within one bubble's render agrees on "this is mark #N". Markdown
 * invokes the helper once per block renderer (p / li / td / th /
 * blockquote); a shared counter keeps the numbering consistent across
 * those calls.
 */
export type MatchCounter = { current: number };

/**
 * Walk a tree of React children and wrap every case-insensitive
 * substring match of `query` with a `<mark>` carrying the
 * `wolf-find-mark` class (Slice 5.0c-i.6 reimplementation).
 *
 * The earlier (5.0c-i.5) version drove the active-mark selection from
 * a DOM-mutation effect that set `data-find-active="true"` on the
 * i-th rendered mark. That had two problems:
 *
 *   1. The attribute was overridden / removed by React reconciliation
 *      on subsequent renders, so the orange highlight vanished
 *      whenever any other state changed.
 *   2. Inline code (`<code>list_agents</code>`) sometimes wasn't
 *      picked up by the helper at all when rehype-highlight wrapped
 *      its contents in `<span class="hljs-…">` — the original walker
 *      handled that fine, but the DOM-mutation timing made it look
 *      like a no-match.
 *
 * This rewrite returns to a React-state-driven approach:
 *
 *   - The helper recurses into HTML React elements (skipping
 *     grounding chips via `data-grounding-chip="true"` and
 *     function-component elements like FencedCodeBlock).
 *   - A counter + activeLocalIdx are passed in; the mark whose index
 *     matches activeLocalIdx is emitted with `data-find-active="true"`
 *     directly in JSX, so the attribute survives every re-render
 *     because React owns it.
 *   - Recursion means the raw-text match count and rendered mark
 *     count agree — which is exactly what MessageThread's matchSpans
 *     enumeration relies on. The drift bug that originally forced
 *     the DOM-based approach is fixed at its root.
 *   - The mark uses ONE class (`wolf-find-mark`) styled in
 *     globals.css. No Tailwind utility classes on the mark, so the
 *     CSS cascade can't fight us — `.wolf-find-mark[data-find-active]`
 *     wins over `.wolf-find-mark` cleanly by specificity.
 */
export function highlightSearchInChildren(
  children: ReactNode,
  query: string,
  activeLocalIdx: number,
  counter: MatchCounter,
): ReactNode {
  if (!query) return children;
  const trimmed = query.trim();
  if (!trimmed) return children;
  const needle = trimmed.toLowerCase();

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
        const myIdx = counter.current++;
        const isActive = myIdx === activeLocalIdx;
        parts.push(
          <mark
            key={`m-${index}-${chunkIdx++}`}
            className="wolf-find-mark"
            data-find-match="true"
            data-find-active={isActive ? "true" : undefined}
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
      // Single opt-out: grounding chips have their own background
      // and shouldn't be split with marks. Slice 5.0c-i.7 removed
      // the previous `typeof el.type !== "string"` guard so we now
      // recurse into function-component elements too — that
      // includes FencedCodeBlock, so a search hit inside a fenced
      // code block gets highlighted just like one in inline code or
      // prose. The component's internal `children` prop receives
      // the highlighted tree via cloneElement; FencedCodeBlock
      // renders it inside its `<code>` and nodeToString still flat-
      // tens marks for the copy-to-clipboard text.
      if (props["data-grounding-chip"]) return child;
      const recursed = highlightSearchInChildren(
        props.children,
        query,
        activeLocalIdx,
        counter,
      );
      return cloneElement(el, undefined, recursed);
    }
    return child;
  });
}
