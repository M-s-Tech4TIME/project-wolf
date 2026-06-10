---
name: per-slice-web-test-checkpoints
description: "Per-slice workflow — fresh reset, self-validation by Claude, reset again, then user manually tests"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

**Restart runbook:** the exact reset + relaunch procedure lives in [`docs/restart.md`](file:///home/alsechemist/Codespace/project-wolf/docs/restart.md). Use it as the source of truth — do NOT re-derive the steps from scratch each cycle. The user explicitly asked (2026-05-29) for this to be reusable so token spend stays low.

For each slice that touches behavior the user will manually web-test:

1. **Implement** the change (with unit tests + lint + mypy + frontend tsc/eslint clean).
2. **Reset to a fresh state** before any test — follow [`docs/restart.md`](file:///home/alsechemist/Codespace/project-wolf/docs/restart.md) Quick version.
3. **Self-validate by Claude before handing over** — hit the API directly with representative prompts (curl `/api/v1/chat`) and verify expected behavior (non-empty answers, no ReadTimeouts, grounding markers behave as designed, etc.). Cross-check numbers against ground truth (DB / Wazuh Discover) when possible. Tail `/tmp/orchestrator.log` for errors.
4. **Reset again** before handing over to the user (same reset steps as #2) so they always test on a fully clean state.
5. **Hand over** with: exact prompts to try, expected outcomes, and honest caveats about what the slice does NOT fix.

**Why:** The user is driving Wolf toward "absolute reliability and stability." Unit tests are not enough — model-call reliability, GPU memory pressure, and emergent qwen3:4b behaviors only show up at runtime. A reproducibly-clean starting state plus a Claude-side smoke run catches problems before they reach the user, which is what makes the manual test efficient instead of frustrating.

**Hardware fact to remember:** 6 GB GPU (5.64 GiB usable). Chat model is `qwen3:4b` (~3.5 GB); grounding judge is `qwen3:8b` (~5 GB). They do NOT fit together — Ollama must swap them on every grounding call, so the first answer after a fresh start (or after an idle period) is slow because qwen3:4b cold-loads. The user has chosen to accept this trade-off rather than weaken the judge. See [[grounding-yellow-vs-red]] for the grounding model context.
