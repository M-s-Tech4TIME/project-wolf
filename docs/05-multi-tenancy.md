# 05 — Multi-Tenancy Isolation Model

A single missing tenant check is not a bug — for an MSSP it is the end of the
company. So the design principle is blunt:

> **Isolation must be the default that you'd have to actively break, never a check
> you have to remember to add.**

Multi-tenancy is built into the foundation. A single-org deployment is "one tenant"
— same code path, no special case. There is no untenanted code anywhere.

## What a tenant is

A tenant is one isolated customer of the platform. Concretely it owns:

- A connection profile to *its* Wazuh deployment (Indexer endpoint, Server API
  endpoint).
- Its own scoped credentials for both.
- Its own slice of the RAG knowledge store.
- Its own audit stream.
- Its own users, approvers, and approval policies.
- Its own model configuration (a tenant may run Claude while another runs Ollama).

## Deployment model

There is a spectrum from cheapest-weakest to costliest-strongest isolation:

| Model | Compute | Data | Isolation strength |
|-------|---------|------|--------------------|
| Pooled | Shared | Shared (rows tagged by `tenant_id`) | Weakest |
| Bridge | Shared | Siloed (per-tenant DB/schema/index) | Strong |
| Siloed | Per-tenant | Per-tenant | Strongest |

**Recommendation: Bridge model with hard data-layer walls regardless.** Pooled
compute is fine; data is siloed per tenant; and the application code still treats
tenancy as if it might be wrong, with defense-in-depth checks at every layer. This
keeps operations sane while making cross-tenant leakage extremely unlikely.

## The core mechanism — tenant context, set once, enforced everywhere

This is the single most important rule in the whole platform:

> **The model never names, picks, or influences which tenant's data is touched. Not
> in a tool argument, not in a parameter, not anywhere. The agent literally does not
> have "tenant" as a knob.**

The flow:

1. User authenticates.
2. Session is bound to a tenant (or the user picks among *their* tenants in the UI,
   before the agent runs).
3. The orchestrator stamps that `tenant_id` into an **immutable request context**.
4. Every tool call the agent makes is intercepted; the tenant context is **injected
   by the orchestrator**, never read from the model's output.
5. If the model's tool call somehow contains a `tenant_id`, the orchestrator
   ignores it and uses the session's. The model's output is **untrusted for
   tenancy**, full stop.

## The four enforcement points — independent, redundant

Tenancy is enforced **redundantly** at every layer that touches data. Each layer
would catch a mistake the others missed.

### 1. Credentials

Each tenant's connection profile holds credentials valid only for that tenant's
Wazuh deployment. The OpenSearch credential for Tenant A cannot authenticate against
Tenant B's indexer — either because the cluster is different, or at minimum because
the role uses document-level security scoped to that tenant's indices. A misrouted
query fails at the connection layer because the credential doesn't open that door.
This is the strongest control: it doesn't depend on application logic being correct.

### 2. Query layer

OpenSearch queries are built by a **query-construction layer** that **forces** the
tenant filter as a mandatory clause. The filter is injected by the builder, not
passed by the caller, and there is **no code path that produces a query without it.
** If pooled at the index level, this is a `tenant_id` term filter wrapped around
whatever the agent asked for. The agent describes *what* to search; the layer
decides *where*, and "where" always includes the tenant wall.

### 3. RAG store

The vector store is partitioned by tenant — separate namespaces, collections, or
indices. A retrieval call carries the tenant context and can only search that
tenant's partition. **This matters more than people expect:** runbooks and past
incident write-ups are some of the most sensitive content in the system, and a
sloppy vector search that ranks across all tenants would surface Client A's incident
history to Client B's analyst. Partition at the storage level, not with a
post-filter on results (post-filtering is a security smell — the data was retrieved,
even if not returned).

### 4. Audit stream

Every audit record is tenant-tagged at write time, and audit reads are themselves
tenant-scoped — an MSSP analyst reviewing the audit trail sees only their tenants.
The audit log is **not exempt** from isolation just because it is infrastructure.

## The independent data-layer re-check

Underneath all four enforcement points, when a result comes back, an independent
check confirms that the tenant stamped on the returned data equals the tenant in
the request context. **If they ever disagree, the request fails closed — hard
error, nothing returned, logged as a security incident.** A tenant mismatch is
never a "degraded but working" state; it is a sign something is fundamentally
wrong, and the system stops.

## The edge cases — where multi-tenancy actually breaks

### Connection-pool bleed

A pooled DB or HTTP connection used for Tenant A is returned to the pool and handed
to a Tenant B request **with Tenant A's session state still on it** — credentials,
search context, a prepared statement. Classic and dangerous.

**Mitigations:**
- **Pool per tenant** (separate pool keyed by tenant, never shared), **or**
- treat every connection checkout as stateless and re-establish tenant context on
  it explicitly before use, with a reset on return.
- Never assume a pooled connection is clean.

### Caching across tenants

An expensive aggregation cached under a key like `alerts_last_24h`. Tenant B's
request hits the cache and gets Tenant A's numbers.

**Mitigation:** `tenant_id` is a **mandatory prefix** of every cache key, enforced
by the cache wrapper. A cache key without a tenant prefix should be impossible to
construct (e.g. raise at the API boundary).

### The LLM context window as a leak vector

Subtle. Within a single conversation this is fine — but if an agent process is
reused, or a context is shared, or requests are batched, residual data from Tenant
A's investigation could sit in context when Tenant B's request runs, and the model
could surface it.

**Mitigations:**
- Agent context is **per-session and per-tenant**, never reused across tenants.
- Context is torn down completely at session end.
- If LLM workers are pooled, the worker is wiped between tenants.
- Treat the context window as tenant-scoped memory.

### Cross-tenant by design (the MSSP overview)

An MSSP wants a "fleet overview across all my clients" dashboard. The instant that
exists, a legitimate cross-tenant read path exists — and that is exactly where an
isolation bug hides, because the feature *is supposed* to cross tenants, just only
within one MSSP's own set.

**Mitigations:**
- Model the MSSP itself as a **parent scope** that owns a defined set of tenant IDs.
- Cross-tenant queries are permitted **only** over the tenant set the requesting
  MSSP account provably owns.
- That set is resolved **server-side from the account**, never from a request
  parameter.
- Results remain tagged per-tenant.
- "Show me everything" never means *everything* — only "everything within my scope."

### Tenant misconfiguration / onboarding

A new tenant is created and, through a config slip, points at another tenant's
Indexer endpoint. Isolation is broken before a single query runs.

**Mitigations:**
- Tenant provisioning is **validated**: on creation, the platform connects with the
  tenant's credentials and confirms the deployment identity matches what is
  expected.
- Connection profiles are **immutable by default** after validation; changes go
  through an audited admin path.

### Noisy-neighbor

Tenant A runs a monstrous query and starves Tenant B of indexer capacity. Not a
data leak, but a real isolation failure — Tenant B's service degraded because of
Tenant A.

**Mitigations:** per-tenant rate limits and query-cost budgets (the same resource
guardrails from `03`, now also serving isolation). One tenant cannot consume
another's share.

### Per-tenant model isolation

If Tenant A uses a hosted API model (Claude/GPT/Gemini) and Tenant B uses a local
Ollama model, both must remain isolated. The model configuration is per tenant; the
model-abstraction adapter is instantiated with the tenant's configuration; no
cross-tenant state lives in adapters.

## Test isolation as a first-class, continuous practice

Don't treat "tenants are isolated" as something verified once. Build an automated
cross-tenant test suite that runs constantly:

- As Tenant A, attempt to read Tenant B's alerts → must fail closed.
- As Tenant A, attempt to retrieve Tenant B's runbooks → must fail closed.
- As Tenant A, attempt to approve Tenant B's proposals → must fail closed.
- As Tenant A, attempt to read Tenant B's audit log → must fail closed.
- Repeat for every read tool, propose tool, and read endpoint.

Run this suite in CI **and** as a synthetic probe in production. Isolation
regressions are silent until catastrophic; continuous testing is the only honest
defense.

## Per-tenant secrets

Each tenant's Wazuh credentials are extremely sensitive — they are keys to a
customer's security infrastructure. They live in a **real secrets manager**
(HashiCorp Vault, OpenBao, or equivalent), encrypted at rest, with per-tenant
access policies, fetched at request time and never held in plaintext config or
logs. If the platform is itself compromised, the blast radius for one tenant's
session must not extend to another tenant's secrets.

## Implementation status — Phase 4 (multi-tenancy hardening)

This section maps the design above to the concrete artifacts that
implement it, as of Phase 4 close-out (2026-05-27). Update it when the
implementation surface changes so future contributors find the
mechanism instead of rebuilding it.

| Design requirement | Implementation |
|---|---|
| Tenant context set once, enforced everywhere | `services/orchestrator/app/tenancy/context.py` — frozen `TenantContext`; `AuthMiddleware` stamps it per request |
| Query-layer forced tenant filter | `app/wazuh/query_builder.py` `TenantScopedQueryBuilder`; no `raw_query()` escape hatch |
| Independent data-layer re-check (fail closed) | `WazuhOpenSearchClient.execute()` raises `TenantMismatchError` if a returned doc's `tenant_id` ≠ request context |
| RAG store partitioned per tenant | `app/knowledge/store.py` `PgvectorKnowledgeStore` — every candidate leg (`_vector_candidates`, `_fts_candidates`, `_vector_aux_candidates`) carries `WHERE tenant_id IS NULL OR tenant_id = :req`; chunk writes validated by `_validate_chunk` (shared corpora forbid a tenant_id, private corpora require one) |
| Caching with mandatory tenant prefix | `app/caching/cache.py` `TenantScopedCache` protocol + `InMemoryTenantCache`. The `_compose_storage_key` helper raises `UnprefixedKeyError` if `tenant_id` is None — an unprefixed key is structurally unconstructable. First consumer: `_resolve_agent_name_to_id` in `app/tools/alerts.py` (60s TTL, per-tenant). Phase 4 Slice 3. |
| Audit stream tenant-tagged at write, scoped on read | `app/audit/log.py` `write_event(..., tenant_id=...)`; `AuditEvent.tenant_id` column. Read + write isolation tested in `tests/test_cross_tenant_isolation.py`. |
| Tenant onboarding validated + immutable post-validation | `app/management/bootstrap_tenant.py` — `_validate_wazuh_connection` probes both endpoints before persisting; `validated_at` stamped on success; re-run for a validated slug refuses without `--update` (`TenantAlreadyExistsError`). `--skip-validation` is the "no Wazuh yet" escape. Phase 4 Slice 2. |
| Connection pooling — pool-per-tenant OR stateless re-establish | **Stateless re-establish.** `app/api/chat.py` opens a fresh `WazuhOpenSearchClient` + `WazuhServerApiClient` per request via async context managers; nothing is pooled across requests, so connection-pool bleed (the doc 05 edge case above) cannot occur. A per-tenant pool is deferred until throughput demands it. |
| Per-tenant model isolation | `app/agent/model_resolver.py` constructs the provider per request from settings; no cross-tenant adapter state. Per-tenant `TenantModelConfig` table (so tenant A runs Claude while tenant B runs Ollama) is a Phase 5+ enhancement — the `get_model_for_tenant(ctx, ...)` signature already takes the tenant context so the seam exists. |
| Continuous isolation testing (CI + synthetic probe) | **CI:** `tests/test_cross_tenant_isolation.py` + `tests/test_tenant_scoped_cache.py` run on every PR (named explicitly in `.github/workflows/ci.yml`). **Synthetic probe:** `tools/tenant_isolation_test/` is a runnable CLI (6 live checks: RAG both directions, audit both directions, cache prefix-rejection, cache cross-tenant) that operators run against a live DB. `make test-isolation` (unit) and `make test-isolation-live` (smoke). Phase 4 Slice 4. |

**Dev two-tenant pattern.** Meaningful isolation testing needs two
tenants with distinct private content. The dev setup bootstraps `acme`
and `beta` (see ONBOARDING Gotcha #8), each seeded via
`app.management.seed_dev_knowledge --tenant-slug <slug>` so each has
private runbook + past-incident chunks that visibly reference its own
name. `tools/tenant_isolation_test` then proves neither can retrieve
the other's chunks.

**Still owed (post-Phase-4):** the per-tenant secrets backend today is
the Fernet-encrypted file backend, not a real secrets manager (Vault /
OpenBao). The `SecretsBackend` protocol abstracts this — swapping in a
Vault backend is a backend implementation, not an application change —
but the production-grade manager itself is Phase 6+ deployment work.
