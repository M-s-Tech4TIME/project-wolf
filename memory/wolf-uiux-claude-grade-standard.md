---
name: wolf-uiux-claude-grade-standard
description: "STANDING FEEDBACK (2026-06-30): Wolf UI/UX must be dynamic/responsive/attractive/robust — Claude-grade conversation UX; recurring fix patterns inside"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

Operator directive (2026-06-30, surfaced via rule_tuning web-test screenshots):
Wolf's UI/UX must be **dynamic, responsive, attractive, lucrative, and robust** —
hold it to **Claude's web conversation UI/UX as the reference bar**, applied
"strictly and profoundly." This is a standing quality bar for every web surface,
not a one-off.

**Why:** the product is operator-facing and must feel professional/polished;
content that breaks layout, can't scroll, or is truncated reads as broken and
undermines trust in Wolf's answers.

**How to apply (recurring patterns — verified fixes, reuse them):**
- **Scrollable panels → native scroll, NOT Radix `ScrollArea`.** Radix's nested
  viewport does not reliably constrain inside our flex chain (Evidence +
  Conversations panels silently didn't scroll; MessageThread already documents
  this and uses the native pattern). Use `min-h-0 flex-1 overflow-y-auto` with
  styled `[&::-webkit-scrollbar]` thumbs. Whenever a region can exceed the
  viewport, it MUST scroll.
- **Wide content must stay INSIDE the message.** A markdown flex child holding
  the answer needs `min-w-0` (else a wide table/code block forces the flex item
  past the column and gets clipped by the thread's `overflow-x-hidden`). Wrap
  markdown tables in an `overflow-x-auto` container; fenced code `pre` already
  scrolls — `min-w-0` on the parent is what makes it engage. Claude-style:
  content scrolls inside its box, never breaks the conversation alignment.
- **No `truncate`/"…" where the user wants the whole thing.** Wrap full content
  within the box (e.g. Actions "Recent activity" detail line: `break-words` on
  its own line, no clamp).
- **Code-block vs inline-code discipline lives in the agent `SYSTEM_PROMPT`**
  (`agent/prompts.py` → `MARKDOWN FORMATTING`): fenced blocks (with a language,
  one command per line) for commands / multi-line code / config / structured
  payloads; inline code ONLY for short literals in prose (ids, IPs, rule ids,
  paths, timestamps); GFM tables for tabular data. Wolf must pick correctly,
  like Claude does.

Source of truth for the shipped fixes: `services/dashboard/components/`
(`citations-panel.tsx`, `chat-sidebar.tsx`, `message-thread.tsx`, `markdown.tsx`),
`services/dashboard/app/actions/page.tsx`, `services/server/wolf_server/agent/prompts.py`.
Honor [[next-dev-cache-vs-build]] when validating the dashboard (tsc+eslint
locally + CI build; .tsx hot-reloads in next dev). Palette per [[wolf-color-palette]].
