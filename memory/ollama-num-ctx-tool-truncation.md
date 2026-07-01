---
name: ollama-num-ctx-tool-truncation
description: "REFERENCE (2026-07-01): Ollama's DEFAULT num_ctx=4096 silently truncates Wolf's ~7.2K-token system-prompt+tool-catalog → model sees no tools → 0 tool calls + 'no such tool' prose. Fix = Settings.ollama_num_ctx (default 16384) applied to chat AND judge builds."
metadata:
  type: reference
---

**Ollama's built-in default context window is only 4096 tokens, and it
TRUNCATES over-long prompts silently — dropping the head.** Wolf's chat prompt
(system prompt + full 14-tool catalog with JSON schemas) is **~7.2K tokens
before any history or tool results**, so at the 4096 default Ollama cut the tool
definitions off the front of the prompt. The model then genuinely could not see
tools like `list_agents` / `search_alerts` / `count_alerts_by_severity` (the
earlier catalog entries) and answered "the available tools do not include…" in
prose with **0 tool calls** — the 2026-07-01 web-test regression across three
questions. The tell in a probe: `input_tokens` pinned at exactly `4096`, and the
model only ever names the *tail* of the catalog (cluster health / active-response
/ knowledge).

**Root cause was ours, not the model.** The chat Ollama adapter was built with
`num_ctx=None` → Ollama default. Only the grounding *judge* had been given
`num_ctx=8192` (Slice 5.0b.4); the chat path was missed when qwen3:8b became the
default primary (a bigger tool catalog + a local model = the two conditions that
made the latent truncation bite). Live-verified: same model + same catalog,
`num_ctx=4096` → `tool_calls=[]`; `8192`/`16384` → correct `list_agents` /
`count_alerts_by_severity`. `input_tokens` at the fixed width = 7200.

**Fix (model-agnostic):** `Settings.ollama_num_ctx` (default **16384**;
`OLLAMA_NUM_CTX` env) — fits the tool prompt plus an 8-step guided loop's
accumulated tool results, far under qwen3's 128K. `get_model_for_organization`
(chat) passes it; the judge shares the SAME knob so a same-tag unified
deployment (default qwen3:8b chat+judge) keeps ONE loaded context and never
reloads between a chat call and its grounding pass. Ignored by hosted providers
(OpenAI/OpenRouter/Anthropic carry large contexts and error loudly rather than
truncate). Regression guard: `test_chat_ollama_build_applies_num_ctx` asserts
the value reaches the built `OllamaAdapter._num_ctx`.

**Reusable rule:** whenever the tool catalog grows (each Phase-6 propose tool
adds schema tokens) or a new local model becomes default, re-check that
`ollama_num_ctx` still comfortably exceeds the bare tool-prompt token count —
raise it for very large environments (bigger tool results), lower only if
VRAM-constrained (larger num_ctx = larger KV-cache VRAM). A future Phase 6.10
model-posture GUI knob. See [[model-failure-resilience-and-openrouter-free-reality]]
+ [[grounding-execution-modes]].
