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
- **Code-block vs inline-code discipline = two layers, both required (specs:
  `reference/HOW_CLAUDE_ORGANIZES_RESPONSES.md` + `reference/WOLF_MARKDOWN_MECHANISM.md`).**
  - *Layer 1 (generation):* `agent/prompts.py` → `SYSTEM_PROMPT` carries a
    `RESPONSE ORGANIZATION` block + `MARKDOWN FORMATTING` rules **plus a WORKED
    EXAMPLE** (a real install procedure: prose + inline `<MANAGER_IP>` + fenced
    `bash` blocks). The worked example is the strongest lever — abstract rules
    ALONE did not stop the model wrapping multi-line commands in single
    backticks; the example fixed it. **The prompt is model-agnostic:** it is
    built ONCE in `agent/loop.py` (`strategy.system_prompt()`) and passed to
    whichever adapter, so ONE edit covers Ollama AND OpenRouter (and any
    provider). NEVER add per-provider prompts. NB: `SYSTEM_PROMPT` is a non-raw
    Python `"""` string — shell `\` line-continuations get eaten; write each
    command on its own full line.
  - *Layer 2 (rendering):* `components/markdown.tsx` is **react-markdown v10**
    (no `inline` prop — the doc's `inline`-prop sample is for ≤v8; do NOT use
    it). It distinguishes fenced (className `language-` / strip `pre`) → code box
    with language label + copy + `overflow-x-auto`, vs inline → pill. Safety-net:
    `looksLikeMisemittedBlock()` promotes a long(>72)+whitespace or multiline
    inline span to a fenced block so a weak model still renders cleanly.
  - Verified PASS on BOTH nemotron (OpenRouter) and qwen3:8b (Ollama) via a
    direct-adapter acceptance test (mech doc §6).

Source of truth for the shipped fixes: `services/dashboard/components/`
(`citations-panel.tsx`, `chat-sidebar.tsx`, `message-thread.tsx`, `markdown.tsx`),
`services/dashboard/app/actions/page.tsx`, `services/server/wolf_server/agent/prompts.py`.
Honor [[next-dev-cache-vs-build]] when validating the dashboard (tsc+eslint
locally + CI build; .tsx hot-reloads in next dev). Palette per [[wolf-color-palette]].
