# 0031 — Provider failover chain (hosted primary → local Ollama safety net)

**Date:** 2026-07-01
**Status:** accepted — mechanism + global config seam implemented. Default ships
Ollama-primary with **no chain**. Per-org wiring is a later phase (see §Out of scope).

## Context

ADR 0030 made OpenRouter a selectable model provider but its `:free` tier proved
**unreliable for Wolf's agentic workload**. Wolf makes many model calls per query
(up to N loop steps + one per grounding claim), so the free-tier cap exhausts fast
and every subsequent call 429s.

**Empirical grounding (live `/key` + chat probes, 2026-07-01, operator's key):**
- The account is `is_free_tier: true` with a **hard cap of 50 free-model requests
  per DAY** (`X-RateLimit-Limit: 50, Remaining: 0`; "Add 10 credits to unlock 1000
  free model requests per day"). The cap is **per-ACCOUNT, shared across every
  `:free` model** — switching to a different free model cannot help.
- Two 429 flavours seen: the account `free-models-per-day` cap (cohere) and
  per-model "temporarily rate-limited upstream" (qwen). Both leave Wolf without an
  answer if that model is the only path.

**Operator decision:** local Ollama is Wolf's **default primary** (reliable,
uncapped, private, reasoning-capable). An organisation *may* configure a hosted
model (e.g. OpenRouter) as **its** primary with its own key; if that hosted model
fails *for any reason*, Wolf must **natively continue on that org's local Ollama** —
the analyst never sees a broken stream. "If OpenRouter fails, we have solid ground
on local Ollama."

## Decision

- **`FailoverProvider`** (`models/failover.py`) composes an ordered chain
  `[primary, fallback, …]` and satisfies the `ModelProvider` protocol, so the agent
  loop and grounding validator are unchanged — they see one model.
  - **Transparent failover on any non-cancellation error** (429/quota, timeout,
    5xx, malformed request, outage): the next link is tried. `except Exception`
    (not `BaseException`) so **`CancelledError` propagates** — the Stop button is
    never swallowed as a "failure."
  - **Clean streaming failover.** The OpenAI/Ollama adapters raise on a `>= 400`
    status *before* the first `ChatStreamDelta`, so a capped primary fails over
    **before any token reaches the client**. A failure *after* a delta was emitted
    re-raises (a half-streamed answer can't be restarted) → the loop's
    `_fail_gracefully` settles the UI honestly (ADR: model-failure resilience).
  - **Per-instance circuit-breaker.** A failed link is skipped for the rest of
    *this* chain instance (one chat request = up to N loop steps), so a capped
    primary is probed **once** per query, not once per step. A fresh instance per
    request re-probes the primary next query — the moment its quota resets, Wolf
    uses it again (no stale lockout).
  - **Conservative capability floor.** `capability()` reports the *safest* profile
    across the chain — `min` step budget and the least-autonomous strategy — because
    any call may land on the weakest link. This keeps the loop from over-driving a
    smaller fallback model (the `budget_exhausted` runaway seen 2026-07-01).
- **Config seam.** `FALLBACK_MODEL_{PROVIDER,ID,API_KEY_REF}` — when
  `FALLBACK_MODEL_ID` is set, the resolver wraps **both** the chat and the
  grounding-judge provider in a `FailoverProvider` (so grounding survives a capped
  hosted judge instead of silently dropping the verdict chips). A pointless
  self-chain (same provider+model as primary) is skipped; fallback provider
  defaults to `ollama`. `check_model_config` validates the fallback at startup.
- **Default = Ollama-primary, no chain.** Wolf's default primary is already local
  Ollama (`qwen3:8b` chat + judge), so there is nothing to fail over *to*; the seam
  ships inert. This is the **single-org** path and keeps single-org ↔ MSSP parity:
  an operator gets failover by setting `DEFAULT_MODEL_*` = OpenRouter +
  `FALLBACK_MODEL_*` = Ollama today, and per-org config populates the same chain
  per-org later.

## Out of scope / tracked

- **Per-organisation model config (later phase).** The org-level "admin picks
  OpenRouter + supplies the org's key, with local-Ollama failover" UI + per-org
  credential storage + per-org chain construction. This ADR ships the *mechanism*
  and the process-wide seam; the per-org *wiring* reuses `FailoverProvider` and the
  reserved `OrganizationContext` seam in `get_model_for_organization`.
- **Process-level circuit-breaker.** Today the breaker is per-request instance
  (skips within one query's steps). A shared cross-request breaker would eliminate
  even the one probe-per-query tax while capped — deferred (per-query re-probe is
  arguably better: it picks the primary back up the instant quota resets).
- The response's reported `model_id` is the chain's declared primary; the **actual
  serving model per call is in the audit trail** (`model.call.success`
  `ChatResponse.model_id`). Surfacing the actually-served model in the UI is a
  follow-on.

## Addendum (2026-07-01) — reactive quota visibility

Failover is the *resilience* half (a capped hosted model continues on local
Ollama). The *visibility* half — telling the org *why* and *what to do* — is
added reactively, without hardcoding any cap number.

**Principle: hardcode the contract, not the numbers.** OpenRouter's free cap is
account-state-dependent (50/day under $10 credits → 1000/day at ≥$10; the
numbers are placeholders in their own docs). So Wolf reads the state LIVE from
the provider's own signals and only hardcodes *how to interpret* them.

- **`models/quota.py`** — a normalized `ProviderQuota` (kind ∈ {`free_daily_cap`,
  `rate_limited`, `credits_exhausted`} + live `limit`/`remaining`/`reset_at`),
  parsed from the `X-RateLimit-{Limit,Remaining,Reset}` headers (Reset =
  epoch **ms**) and the 429/402 body (`free-models-per-day`). `user_message()`
  emits the actionable line (remaining + humanized reset + remedy).
- **`models/openai.py`** — `_provider_error` reads the headers and splits **402**
  (`ModelProviderPaymentRequiredError`) from **429**; both carry `.quota`.
- **`agent/loop.py`** — `_model_failure_message` is quota-aware (sole-provider
  exhaustion → actionable analyst message today).
- **`models/failover.py`** — `model_failover_link_failed` records the quota
  kind/remaining/limit (auditable degradation).

**Deferred to the per-org OpenRouter phase:** the *proactive* `GET /api/v1/key`
+ `/credits` dashboard indicator, and the *successful-failover* in-chat chip
("OpenRouter daily cap reached — answered on local Ollama, resets in Nh"). Both
need an active per-org OpenRouter primary to be exercised/web-tested; they reuse
the `ProviderQuota` contract + the failover log signal built here rather than
shipping dormant, untested UI now.
