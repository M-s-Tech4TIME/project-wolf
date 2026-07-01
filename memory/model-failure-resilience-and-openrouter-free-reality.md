---
name: model-failure-resilience-and-openrouter-free-reality
description: "REFERENCE (2026-07-01): SSE chat must degrade gracefully on ANY model failure (never hang); OpenRouter FREE tier is unreliable for Wolf's agent load — local Ollama is the dependable fallback"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

**Two linked facts from the 2026-07-01 hang incident** (chat stuck on "Step 1/15:
thinking…" forever, Stop dead).

**1. SSE resilience invariant — a model failure must NEVER hang the stream.**
The agent loop's model-call `except` (`services/server/wolf_server/agent/loop.py`)
routes failures through `_fail_gracefully()` → emits `model.call.failed` + a settled
`answer` (readable, non-leaky message via `_model_failure_message`; raw detail →
logs+audit only). A `WolfError` re-raises **only** in blocking mode
(`event_callback is None` = `POST /chat`, so the API maps it to a clean HTTP error);
in streaming mode it must degrade gracefully, because the SSE response has already
started and a raise becomes Starlette's `RuntimeError: response already started` →
broken stream → the browser hangs with Stop dead. Belt-and-braces: `api/chat.py`
`runner()` catches anything else raised after the response started and emits a
terminal `error` LoopEvent (new event type, added to both `agent/events.py`
`LoopEventType` and the frontend `lib/types.ts` union + a `case "error"` in
`hooks/use-conversation-streams.ts` that sets `phase:"error"`). Regression tests in
`tests/test_loop_events.py` (streaming degrades gracefully / blocking re-raises /
generic handled) + `tests/test_agent_loop.py` (clean user content, raw detail in the
audit row). **Do not reintroduce a bare `raise` on the streaming path.**

**2. OpenRouter FREE tier is NOT dependable for an agentic workload.** Wolf makes
many model calls per query (up to 15 loop steps + grounding), so free daily/upstream
rate caps bite fast. Every free model tried this week failed at some point:
`openrouter/owl-alpha` → 404 (retired); `nvidia/nemotron-3-ultra-550b-a55b:free` →
400 "DEGRADED function" on tool calls (plain chat 200, WITH tools 400 — so tool-using
queries fail); `qwen/qwen3-coder:free` + `qwen/qwen3-next-80b-a3b-instruct:free` →
429. Probe tool-calling with an actual tools payload (advertised `tools=True` in
`/models` does NOT guarantee the free endpoint serves tool calls). Run probes from
`services/server` (the `SECRETS_FILE_PATH=./.local/secrets.enc` is RELATIVE — a wrong
CWD gives an empty key → misleading 401). **Local Ollama (qwen3:8b, uncapped, working
tool-calling) is the reliable fallback** — one env flip (`DEFAULT_MODEL_ID=qwen3:8b`,
provider `ollama`, key ref empty).

**Current wired stack (2026-07-01):** `KNOWN_MODELS` has all three free models
(tools=full, frontier). Active chat = `cohere/north-mini-code:free` (the only one
serving tool-calls at wiring time — verified a correct `list_agents` call + formatting
acceptance PASS). Active judge = `qwen/qwen3-next-80b-a3b-instruct:free` (only free
candidate with native `structured_outputs` → best-fit JSON judge; deliberately a
DIFFERENT model from chat to spread the free-tier cap). Best-fit when un-throttled:
chat=`qwen/qwen3-coder:free` (480B agentic coder) + judge=qwen3-next. Models are
configurable via registry+env today; the dynamic GUI selector is Phase 6.10
([[config-settings-system-phase]], "Model posture"). See ADR 0030 + [[grounding-execution-modes]].
