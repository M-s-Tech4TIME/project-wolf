# 0030 — OpenRouter as a selectable model provider (chat + grounding)

**Date:** 2026-06-29
**Status:** accepted — OR.1 implemented (chat + grounding). **OR.2 (embeddings)
DECIDED AGAINST** (2026-06-29): embeddings stay on local nomic-embed — the
free-tier daily cap is shared across all OpenRouter calls, so a corpus re-embed +
per-query embedding is impractical (see §Out of scope). The OpenRouter work is
complete with OR.1.

## Context

The operator wants the option to run chat + grounding (and, later, embeddings) on
**free, open frontier models via OpenRouter** instead of only local Ollama —
specifically `nvidia/nemotron-3-ultra-550b-a55b:free` and `openrouter/owl-alpha`,
both selectable. Requirement: it must be a **configurable option, not a
replacement** — local Ollama stays the default — and (a later phase) per-org with
the org's own OpenRouter key (Phase 6.10).

**Empirical grounding (live OpenRouter probe, 2026-06-29, operator's key):**
- Both chat models exist and advertise **native tool-calling** + ~1M-token
  context; both `:free` cost **$0**. Verified end-to-end against the live API:
  blocking chat, SSE streaming, and **streamed tool-calling** all work.
- "Free **and** uncapped" does **not** exist on OpenRouter: every `:free` model
  shares the free-tier **daily request cap** (~50/day with no deposit, 1000/day
  with ≥$10 lifetime credits). The key is `is_free_tier: true`. Only local Ollama
  is free **and** uncapped **and** on-prem. Operator accepted the cap for the
  *option* (default stays Ollama).
- Free models **may log/train on prompts** — a data-governance tradeoff for a SOC
  product sending Wazuh alert data off-prem. Surfaced; operator accepted.
- Embeddings ARE reachable (OpenRouter has a hidden `/embeddings` route — the
  public chat catalog lists none): `nvidia/llama-nemotron-embed-vl-1b-v2:free`
  returned a real **2048-dim** vector at $0. But Wolf's `knowledge_chunks.embedding`
  is a **hard 768-dim** pgvector contract → adopting it needs a dimension
  migration + full corpus re-embed (OR.2, not a toggle).

## Decision (OR.1)

- **OpenRouter is the OpenAI Chat Completions wire protocol.** The shared
  `OpenAIAdapter` (renamed to `openrouter.py`? — **no**: it serves OpenAI/Azure/
  vLLM/LM Studio/LocalAI too; the name is the protocol, not the vendor) carries
  all the wire logic. It gained **real `chat_stream`** (SSE deltas + accumulated
  tool-calls, matching the Ollama adapter), **429/rate-limit + transport error
  handling** (`ModelProviderRateLimitError` → a clear "daily cap reached, switch
  to local Ollama" message instead of an opaque 500), and an `extra_headers` hook.
- **`OpenRouterAdapter`** (`models/openrouter.py`) is a thin, *discoverable*
  subclass that pins the OpenRouter base (`https://openrouter.ai/api`, adapter
  posts `/v1/chat/completions`) + attribution headers (`HTTP-Referer`/`X-Title`)
  + `provider="openrouter"`. A new `openrouter` case in the model_resolver factory
  builds it; the API key comes from the secrets backend (`model.openrouter.api_key`).
- Both models are registered in `KNOWN_MODELS` (`provider="openrouter"`, 1M ctx,
  native tools full; nemotron `prompt_coaxed` structured output + `restricted`
  NVIDIA license; owl-alpha `schema_enforced` + `proprietary`/stealth).
- **Default stays local Ollama** (free, uncapped, on-prem). Flip per deployment via
  `DEFAULT_MODEL_PROVIDER` / `GROUNDING_JUDGE_MODEL_PROVIDER` = `openrouter` +
  the model id + `*_API_KEY_REF=model.openrouter.api_key`. Chat and grounding can
  select OpenRouter independently (the resolver already split them).

## Out of scope / tracked

- **OR.2 — embeddings via OpenRouter: DECIDED AGAINST (2026-06-29).** Technically
  reachable (the 2048-dim model returns vectors at $0), but the shared free-tier
  daily cap makes it impractical: a corpus re-embed is one call *per chunk*
  (hundreds–thousands → blows the ~50/day cap, needs a deposit or many days), and
  per-query embedding would compete with chat + grounding for the same cap — plus
  it sends knowledge content off-prem to a logging model and needs a 768→2048
  pgvector migration. Local nomic-embed (free, uncapped, fast, on-prem, already
  correct) wins. Embeddings stay local; revisit only if a paid/uncapped embeddings
  path is adopted.
- **Per-org provider + key (Phase 6.10):** each org configures its own OpenRouter
  key + model via the synced Superuser/settings surface; this slice is process-wide.
- True token-by-token streaming depends on the upstream route — some `:free`
  routes buffer and emit content in one chunk (the adapter streams whatever
  granularity it receives; degrades gracefully to ~blocking UX).
