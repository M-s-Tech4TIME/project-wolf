---
name: no-hard-step-caps-unbounded-persistence
description: "OPERATOR DIRECTIVE (2026-07-06, from the 6-f.4 web-test; SHIPPED same day in 6-f.5): never hard-cap Wolf's agent step count at a fixed value — utilize step counting, but persist until satisfied; every stop path ends in best-effort synthesis, never a canned 'budget exhausted'"
metadata:
  type: feedback
---

**Directive (2026-07-06, 6-f.4 web-test):** a generic request ("change tracecat integration to level 3") died at 8 steps with "The step budget was exhausted…" + Non-factual chips despite 8 tool calls / 119K tokens of good docs-first evidence. Operator: "why are we limiting wolf's budget through steps… we can definitely utilize the step count, but never limit the step count to any specific value." And beyond config authoring: for ANY task — generic, partially specific, or fully specific — Wolf must "dynamically think, reason, verify, justify, validate and then respond very robustly with the best possible answer."

**Why:** web research + authoring loops legitimately need many iterations; a fixed cap turns gathered evidence into a canned apology. The live 8 came from the failover chain's conservative floor (`min()` across links → qwen3:8b's `max_safe_autonomous_steps=8` beat cohere's 15).

**How to apply:** the loop runs until answer / no-progress / context-full — never `for step in range(budget)` as a wall; `max_safe_autonomous_steps` becomes telemetry + strategy signal only; EVERY stop path ends in forced best-effort synthesis from the evidence already gathered (kill the canned `budget_exhausted` message + chip); runaway protection = the redundant-tool-call guard (already in `agent/loop.py`) + an operator-configurable circuit breaker (env → Phase 6.10 GUI consumer), never a hardcoded number. The web-research per-request budget stays a separate A6 self-protection knob (already env-tunable via `web_search_budget_per_request`) — raise it generously and give it the same graceful degradation. Related: [[web-research-as-universal-power]], [[grounding-enrichment-tools-future-phase]].

**SHIPPED 2026-07-06 (slice 6-f.5, ADR 0032 addendum):** exactly as above — `while True` loop; context-fit guard at `AGENT_CONTEXT_FIT_THRESHOLD` (0.8) × `effective_context_window()` (Ollama = loaded num_ctx, FailoverProvider = chain min); `AGENT_STEP_BREAKER` (default 0 = off); `_synthesized_stop` best-effort synthesis on every forced stop; `_CHECKPOINT_NUDGE` at the graded cadence; web budget 12→32; SYSTEM_PROMPT #7 PERSIST UNTIL SATISFIED; `budget_exhausted` retired (TS union keeps it for legacy rows).
