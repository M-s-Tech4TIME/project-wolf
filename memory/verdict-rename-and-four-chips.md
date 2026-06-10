---
name: verdict-rename-and-four-chips
description: Slice 5.0c will rename the four grounding verdicts and give EACH one its own inline chip (currently only two of four are chipped)
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

User decided (2026-05-28) on the final naming + a four-chip display.

| Internal verdict | User-facing label | Chip color | Icon |
|---|---|---|---|
| `supported` | **Verified** | 🟢 emerald (`emerald-500/15`, `text-emerald-700`) | `Check` |
| `unverifiable` (preamble/transitions) | **Non-factual** | 🟡 muted/soft yellow (lighter than uncertain — to distinguish) | `MessageCircle` |
| `uncertain` | **Uncertain** | 🟡 amber (`amber-400/20`, `text-amber-700`) | `Info` |
| `unsupported` | **Not Verified** | 🔴 destructive | `AlertTriangle` |

**Implementation note:** today only `uncertain` and `unsupported` emit inline markers. The two new chips require backend annotation for `supported` and `unverifiable` too.

- Backend: extend `_VERDICT_MARKER` in [validator.py](file:///home/alsechemist/Codespace/project-wolf/services/orchestrator/app/grounding/validator.py) to include `supported` → `[verified]` and `unverifiable` → `[non-factual]`. Add module constants `MARKER_VERIFIED` and `MARKER_NON_FACTUAL` next to the existing pair.
- Frontend: extend `GROUNDING_MARKERS` in [markdown.tsx](file:///home/alsechemist/Codespace/project-wolf/frontend/components/markdown.tsx) and the `MARKER_SPLIT` regex to recognise all four tokens.
- `GroundingBadge` in [message-thread.tsx](file:///home/alsechemist/Codespace/project-wolf/frontend/components/message-thread.tsx): tooltip wording uses the new labels (Verified / Uncertain / Not Verified / Non-factual).
- Internal verdict names (`supported`/`unverifiable`/`uncertain`/`unsupported`) stay unchanged — only display labels and marker tokens emitted change. This keeps old answers in the DB rendering without migration.

**Why two different yellows:** uncertain and non-factual both warrant a heads-up but mean different things. Lighter yellow + a chat/transition icon for non-factual signals "preamble, not a claim" without the cautionary weight of uncertain. If the user later prefers a totally neutral color (gray/blue) for non-factual, swap chip styling; this is a low-risk cosmetic change.

Batched into Slice 5.0c alongside the UI overhaul (Evidence panel, sidebar, avatar, etc.). See [[per-slice-web-test-checkpoints]] for the per-slice workflow.
