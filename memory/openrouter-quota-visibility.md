---
name: openrouter-quota-visibility
description: "REFERENCE (2026-07-01, ADR 0031 addendum): OpenRouter quota/credit visibility = hardcode the CONTRACT not the NUMBERS. Reactive half shipped (ProviderQuota from 429/402 headers+body → actionable msg); proactive /key+/credits dashboard + failover chip deferred to per-org OpenRouter phase."
metadata: 
  node_type: memory
  type: reference
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

**Operator design question (2026-07-01): should Wolf hardcode OpenRouter's
limits so it can warn on exhaustion? Answer = hardcode the CONTRACT, never the
NUMBERS.** The free cap is account-state-dependent (VERIFIED: 50 req/day under
$10 lifetime credits → 1000/day at ≥$10 — it FLIPS on balance) and OpenRouter
changes the figures over time (placeholders even in their own docs). A hardcoded
constant is wrong the moment an org adds credits. So Wolf reads the state LIVE
and only hardcodes *how to interpret* the signals — same posture as the
[[wazuh-active-response-contract]] and the KNOWN_MODELS registry.

**Live signal sources.** (a) Reactive, at call time: the `X-RateLimit-{Limit,
Remaining,Reset}` headers OpenRouter stamps on a **429** (`Reset` = Unix epoch
in **MILLISECONDS** — disambiguate from seconds by magnitude) + the error body
(`free-models-per-day` = the shared free-tier daily cap vs a per-model
"temporarily rate-limited upstream"); and a **402** = negative credit balance.
(b) Proactive (deferred): `GET /api/v1/key` → `limit`/`limit_remaining`/
`limit_reset`/`usage`/`is_free_tier`, and `GET /api/v1/credits` → balance.

**Shipped (reactive half — operator chose "reactive now, proactive later"):**
- `models/quota.py` — normalized `ProviderQuota{provider, kind, limit,
  remaining, reset_at, is_free_tier, detail}`; `QuotaKind` ∈
  {free_daily_cap, rate_limited, credits_exhausted}; `quota_from_response(...)`
  classifier (returns None for non-429/402, never raises); `user_message()` =
  actionable line (live remaining + humanized "resets in 3h 20m" + remedy).
- `models/openai.py` — `_provider_error` reads response headers + splits **402**
  (`ModelProviderPaymentRequiredError`) from **429**; both carry `.quota`.
  OpenRouterAdapter inherits it.
- `agent/loop.py` — `_model_failure_message` is quota-aware (sole-provider
  exhaustion → actionable analyst message TODAY, no fallback needed).
- `models/failover.py` — `model_failover_link_failed` log records
  `quota_kind`/`quota_remaining`/`quota_limit` (auditable degradation).
- Tests: `test_provider_quota.py` (13) — classification, ms/s reset parse,
  past-reset omission, 429/402 attachment, adapter MockTransport replaying the
  REAL 429 headers+body, quota-aware loop message.

**Empirical confirmation (2026-07-02, live).** `GET /key` showed `usage_daily=0`
+ `is_free_tier=true` (account cap NOT touched) YET **both** `qwen/qwen3-coder:free`
+ `qwen/qwen3-next-80b-a3b-instruct:free` returned **429 "temporarily rate-limited
upstream"** — proving the two limits are INDEPENDENT: a fresh daily budget does NOT
guarantee a given `:free` route is usable (the upstream provider throttles the route
globally). At the same moment `cohere/north-mini-code:free` was **200/live** → it's the
reliable live-fallback chat/judge when the qwen free routes are hot. Practical takeaway
for "use OpenRouter for faster web-tests": always **smoke each `:free` route first**
(the account quota check alone is misleading), and wire local Ollama as
`FALLBACK_MODEL_*` so a mid-test 429 degrades instead of breaking. Dependable hosted =
paid credits (leaves free-tier). Dev `.env` switch (gitignored): DEFAULT/GROUNDING_JUDGE
`_MODEL_PROVIDER=openrouter` + `_MODEL_ID=<route>` + `_API_KEY_REF=model.openrouter.api_key`;
revert = set both back to `ollama`/`qwen3:8b`.

**Deferred to the per-org OpenRouter config phase (documented, not dropped):**
the *proactive* `/key`+`/credits` Models/Settings indicator, and the
*successful-failover* in-chat chip ("OpenRouter daily cap reached — answered on
local Ollama, resets in Nh"). Both need an ACTIVE per-org OpenRouter primary to
be exercised/web-tested — shipping that UI now = dormant untested surface (the
failover path is inert under today's Ollama default). They reuse the
`ProviderQuota` contract + the failover log signal built here. See ADR 0031
addendum + [[model-failure-resilience-and-openrouter-free-reality]].
