---
name: model-failure-resilience-and-openrouter-free-reality
description: "REFERENCE: SSE chat must degrade gracefully on ANY model failure; OpenRouter FREE = hard 50/day PER-ACCOUNT cap (no free model escapes it); resolved by a FailoverProvider (hosted primary → local Ollama) — ADR 0031"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

**Three linked facts: SSE resilience, the OpenRouter free-tier reality, and the
failover chain that resolves it (ADR 0031, 2026-07-01).**

**1. SSE resilience invariant — a model failure must NEVER hang the stream.**
The agent loop's model-call `except` (`agent/loop.py`) routes failures through
`_fail_gracefully()` → emits `model.call.failed` + a settled `answer` (readable,
non-leaky via `_model_failure_message`; raw detail → logs+audit only). A `WolfError`
re-raises **only** in blocking mode (`event_callback is None` = `POST /chat`); in
streaming mode it MUST degrade gracefully — the SSE response has already started, so
a raise becomes Starlette `RuntimeError: response already started` → broken stream →
browser hangs with Stop dead. Belt-and-braces: `api/chat.py` `runner()` emits a
terminal `error` LoopEvent (type in both `agent/events.py` + frontend `lib/types.ts`
+ `case "error"` in `hooks/use-conversation-streams.ts`). **Never a bare `raise` on
the streaming path.**

**2. OpenRouter FREE tier — verified HARD CAP, per-ACCOUNT.** The `/api/v1/key`
endpoint (2026-07-01, operator's key) returns `is_free_tier: true` and 429s say
verbatim: *"Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000
free model requests per day"* with `X-RateLimit-Limit: 50, Remaining: 0`. The cap is
**50 requests/DAY shared across EVERY `:free` model** (1000/day with ≥$10 lifetime
credits) — **per-account, NOT per-model**, so switching free models CANNOT help.
Wolf makes many calls/query (≤N loop steps + 1/grounding-claim) → exhausts fast.
Two 429 flavours: account `free-models-per-day` (cohere) + per-model "temporarily
rate-limited upstream" (qwen). **No free OpenRouter model is "no cap / no
rate-limit"** — that requires PAID credits (per-token, generous limits) or **local
Ollama** (uncapped, private, reasoning-capable — the only thing meeting no-cap +
no-data-egress + $0). Probe from `services/server` (`SECRETS_FILE_PATH` is RELATIVE →
wrong CWD = empty key = misleading 401); source repo-root `.env` first; `.get()` is
async.

**3. FailoverProvider — the resolution (ADR 0031).** `models/failover.py` composes an
ordered chain `[primary, fallback]` satisfying the `ModelProvider` protocol (loop +
grounding unchanged). Invariants: fails over on ANY non-cancellation error
(`except Exception`, so **`CancelledError` propagates** → Stop button safe); **clean
pre-stream failover** (OpenAI/Ollama adapters raise on `>=400` BEFORE the first
`ChatStreamDelta`; a post-delta failure re-raises → `_fail_gracefully`);
**per-instance circuit-breaker** (a failed link is skipped for the rest of THIS
query's steps → primary probed once/query not once/step; fresh instance/request
re-probes so a reset quota is picked up immediately); **conservative capability
floor** (`capability()` = min step budget + least-autonomous strategy across links →
never over-drives a weaker fallback, also fixes the `budget_exhausted` runaway).
Wired via `FALLBACK_MODEL_{PROVIDER,ID,API_KEY_REF}` for BOTH chat + judge; resolver
`_build_with_optional_fallback` skips a pointless self-chain; `check_model_config`
validates it at startup. **Default posture (operator, 2026-07-01):** local Ollama
`qwen3:8b` is the DEFAULT primary for chat AND judge (no chain — nothing to fail over
to). OpenRouter is a selectable primary; **per-org**, an org admin configures
OpenRouter with the org's own key and gets automatic Ollama failover — but per-org
model config is a LATER PHASE (this ships the mechanism + the global single-org seam;
single-org ↔ MSSP parity). Live-verified: OpenRouter 429 → failed over to Ollama
`qwen3:8b` with a correct `list_agents` tool call (chat + stream).

**Two reusable bug/gotcha fixes that shipped alongside (2026-07-01):**
- **Empty-message 400** (real bug, any OpenAI-compatible provider): an INTERRUPTED
  turn ("no response generated before stop") is stored with empty content;
  `historyUpTo` replays it and `_message_to_openai` emitted
  `{"role":"assistant","content":""}` → Cohere/OpenRouter 400 "invalid message" at
  step 0. Fix (model-agnostic): loop skips empty/whitespace history turns +
  serializers coerce `None→""` and drop empty non-tool messages (`_sendable`).
- **Repeated-tool-call guard** (`agent/loop.py`): a weak model looping the same tool
  call (same name+args) to `budget_exhausted` is now nudged, then force-synthesized
  after 2 consecutive redundant steps (`_MAX_REDUNDANT_STREAK`). Model-agnostic.
- **`grounding_unavailable` signal**: `grounding.completed` with `ran=false` (judge
  failed, distinct from "nothing to verify" which emits no event) now shows an honest
  "grounding unavailable" chip instead of silently nothing (frontend flag set in
  `updateAssistantGrounding`).

See ADR 0030 (OpenRouter provider) + ADR 0031 (failover) + [[grounding-execution-modes]]
+ [[config-settings-system-phase]] (Phase 6.10 GUI for model posture).
