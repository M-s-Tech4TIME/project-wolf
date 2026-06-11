# 05 — Multi-Tenancy Isolation Model

A single missing organization check is not a bug — for an MSSP it is the end of the
company. So the design principle is blunt:

> **Isolation must be the default that you'd have to actively break, never a check
> you have to remember to add.**

Multi-organization is built into the foundation. A single-org deployment is "one organization"
— same code path, no special case. There is no unorganizationed code anywhere.

## What a organization is

A organization is one isolated customer of the platform. Concretely it owns:

- A connection profile to *its* Wazuh deployment (Indexer endpoint, Server API
  endpoint).
- Its own scoped credentials for both.
- Its own slice of the RAG knowledge store.
- Its own audit stream.
- Its own users, approvers, and approval policies.
- Its own model configuration (a organization may run Claude while another runs Ollama).

## Deployment model

There is a spectrum from cheapest-weakest to costliest-strongest isolation:

| Model | Compute | Data | Isolation strength |
|-------|---------|------|--------------------|
| Pooled | Shared | Shared (rows tagged by `organization_id`) | Weakest |
| Bridge | Shared | Siloed (per-organization DB/schema/index) | Strong |
| Siloed | Per-organization | Per-organization | Strongest |

**Recommendation: Bridge model with hard data-layer walls regardless.** Pooled
compute is fine; data is siloed per organization; and the application code still treats
tenancy as if it might be wrong, with defense-in-depth checks at every layer. This
keeps operations sane while making cross-organization leakage extremely unlikely.

## The core mechanism — organization context, set once, enforced everywhere

This is the single most important rule in the whole platform:

> **The model never names, picks, or influences which organization's data is touched. Not
> in a tool argument, not in a parameter, not anywhere. The agent literally does not
> have "organization" as a knob.**

The flow:

1. User authenticates.
2. Session is bound to a organization (or the user picks among *their* organizations in the UI,
   before the agent runs).
3. The orchestrator stamps that `organization_id` into an **immutable request context**.
4. Every tool call the agent makes is intercepted; the organization context is **injected
   by the orchestrator**, never read from the model's output.
5. If the model's tool call somehow contains a `organization_id`, the orchestrator
   ignores it and uses the session's. The model's output is **untrusted for
   tenancy**, full stop.

## The four enforcement points — independent, redundant

Tenancy is enforced **redundantly** at every layer that touches data. Each layer
would catch a mistake the others missed.

### 1. Credentials

Each organization's connection profile holds credentials valid only for that organization's
Wazuh deployment. The OpenSearch credential for Organization A cannot authenticate against
Organization B's indexer — either because the cluster is different, or at minimum because
the role uses document-level security scoped to that organization's indices. A misrouted
query fails at the connection layer because the credential doesn't open that door.
This is the strongest control: it doesn't depend on application logic being correct.

### 2. Query layer

OpenSearch queries are built by a **query-construction layer** that **forces** the
organization filter as a mandatory clause. The filter is injected by the builder, not
passed by the caller, and there is **no code path that produces a query without it.
** If pooled at the index level, this is a `organization_id` term filter wrapped around
whatever the agent asked for. The agent describes *what* to search; the layer
decides *where*, and "where" always includes the organization wall.

### 3. RAG store

The vector store is partitioned by organization — separate namespaces, collections, or
indices. A retrieval call carries the organization context and can only search that
organization's partition. **This matters more than people expect:** runbooks and past
incident write-ups are some of the most sensitive content in the system, and a
sloppy vector search that ranks across all organizations would surface Client A's incident
history to Client B's analyst. Partition at the storage level, not with a
post-filter on results (post-filtering is a security smell — the data was retrieved,
even if not returned).

### 4. Audit stream

Every audit record is organization-tagged at write time, and audit reads are themselves
organization-scoped — an MSSP analyst reviewing the audit trail sees only their organizations.
The audit log is **not exempt** from isolation just because it is infrastructure.

## The independent data-layer re-check

Underneath all four enforcement points, when a result comes back, an independent
check confirms that the organization stamped on the returned data equals the organization in
the request context. **If they ever disagree, the request fails closed — hard
error, nothing returned, logged as a security incident.** A organization mismatch is
never a "degraded but working" state; it is a sign something is fundamentally
wrong, and the system stops.

## The edge cases — where multi-organization actually breaks

### Connection-pool bleed

A pooled DB or HTTP connection used for Organization A is returned to the pool and handed
to a Organization B request **with Organization A's session state still on it** — credentials,
search context, a prepared statement. Classic and dangerous.

**Mitigations:**
- **Pool per organization** (separate pool keyed by organization, never shared), **or**
- treat every connection checkout as stateless and re-establish organization context on
  it explicitly before use, with a reset on return.
- Never assume a pooled connection is clean.

### Caching across organizations

An expensive aggregation cached under a key like `alerts_last_24h`. Organization B's
request hits the cache and gets Organization A's numbers.

**Mitigation:** `organization_id` is a **mandatory prefix** of every cache key, enforced
by the cache wrapper. A cache key without a organization prefix should be impossible to
construct (e.g. raise at the API boundary).

### The LLM context window as a leak vector

Subtle. Within a single conversation this is fine — but if an agent process is
reused, or a context is shared, or requests are batched, residual data from Organization
A's investigation could sit in context when Organization B's request runs, and the model
could surface it.

**Mitigations:**
- Agent context is **per-session and per-organization**, never reused across organizations.
- Context is torn down completely at session end.
- If LLM workers are pooled, the worker is wiped between organizations.
- Treat the context window as organization-scoped memory.

### Cross-organization by design (the MSSP overview)

An MSSP wants a "fleet overview across all my clients" dashboard. The instant that
exists, a legitimate cross-organization read path exists — and that is exactly where an
isolation bug hides, because the feature *is supposed* to cross organizations, just only
within one MSSP's own set.

**Mitigations:**
- Model the MSSP itself as a **parent scope** that owns a defined set of organization IDs.
- Cross-organization queries are permitted **only** over the organization set the requesting
  MSSP account provably owns.
- That set is resolved **server-side from the account**, never from a request
  parameter.
- Results remain tagged per-organization.
- "Show me everything" never means *everything* — only "everything within my scope."

### Organization misconfiguration / onboarding

A new organization is created and, through a config slip, points at another organization's
Indexer endpoint. Isolation is broken before a single query runs.

**Mitigations:**
- Organization provisioning is **validated**: on creation, the platform connects with the
  organization's credentials and confirms the deployment identity matches what is
  expected.
- Connection profiles are **immutable by default** after validation; changes go
  through an audited admin path.

### Noisy-neighbor

Organization A runs a monstrous query and starves Organization B of indexer capacity. Not a
data leak, but a real isolation failure — Organization B's service degraded because of
Organization A.

**Mitigations:** per-organization rate limits and query-cost budgets (the same resource
guardrails from `03`, now also serving isolation). One organization cannot consume
another's share.

### Per-organization model isolation

If Organization A uses a hosted API model (Claude/GPT/Gemini) and Organization B uses a local
Ollama model, both must remain isolated. The model configuration is per organization; the
model-abstraction adapter is instantiated with the organization's configuration; no
cross-organization state lives in adapters.

## Test isolation as a first-class, continuous practice

Don't treat "organizations are isolated" as something verified once. Build an automated
cross-organization test suite that runs constantly:

- As Organization A, attempt to read Organization B's alerts → must fail closed.
- As Organization A, attempt to retrieve Organization B's runbooks → must fail closed.
- As Organization A, attempt to approve Organization B's proposals → must fail closed.
- As Organization A, attempt to read Organization B's audit log → must fail closed.
- Repeat for every read tool, propose tool, and read endpoint.

Run this suite in CI **and** as a synthetic probe in production. Isolation
regressions are silent until catastrophic; continuous testing is the only honest
defense.

## Per-organization secrets

Each organization's Wazuh credentials are extremely sensitive — they are keys to a
customer's security infrastructure. They live in a **real secrets manager**
(HashiCorp Vault, OpenBao, or equivalent), encrypted at rest, with per-organization
access policies, fetched at request time and never held in plaintext config or
logs. If the platform is itself compromised, the blast radius for one organization's
session must not extend to another organization's secrets.

## Implementation status — Phase 4 (multi-organization hardening)

This section maps the design above to the concrete artifacts that
implement it, as of Phase 4 close-out (2026-05-27). Update it when the
implementation surface changes so future contributors find the
mechanism instead of rebuilding it.

| Design requirement | Implementation |
|---|---|
| Organization context set once, enforced everywhere | `services/orchestrator/app/tenancy/context.py` — frozen `OrganizationContext`; `AuthMiddleware` stamps it per request |
| Query-layer forced organization filter | `app/wazuh/query_builder.py` `OrganizationScopedQueryBuilder`; no `raw_query()` escape hatch |
| Independent data-layer re-check (fail closed) | `WazuhOpenSearchClient.execute()` raises `OrganizationMismatchError` if a returned doc's `organization_id` ≠ request context |
| RAG store partitioned per organization | `app/knowledge/store.py` `PgvectorKnowledgeStore` — every candidate leg (`_vector_candidates`, `_fts_candidates`, `_vector_aux_candidates`) carries `WHERE organization_id IS NULL OR organization_id = :req`; chunk writes validated by `_validate_chunk` (shared corpora forbid a organization_id, private corpora require one) |
| Caching with mandatory organization prefix | `app/caching/cache.py` `OrganizationScopedCache` protocol + `InMemoryOrganizationCache`. The `_compose_storage_key` helper raises `UnprefixedKeyError` if `organization_id` is None — an unprefixed key is structurally unconstructable. First consumer: `_resolve_agent_name_to_id` in `app/tools/alerts.py` (60s TTL, per-organization). Phase 4 Slice 3. |
| Audit stream organization-tagged at write, scoped on read | `app/audit/log.py` `write_event(..., organization_id=...)`; `AuditEvent.organization_id` column. Read + write isolation tested in `tests/test_cross_organization_isolation.py`. |
| Organization onboarding validated + immutable post-validation | `app/management/bootstrap_organization.py` — `_validate_wazuh_connection` probes both endpoints before persisting; `validated_at` stamped on success; re-run for a validated slug refuses without `--update` (`OrganizationAlreadyExistsError`). `--skip-validation` is the "no Wazuh yet" escape. Phase 4 Slice 2. |
| Connection pooling — pool-per-organization OR stateless re-establish | **Stateless re-establish.** `app/api/chat.py` opens a fresh `WazuhOpenSearchClient` + `WazuhServerApiClient` per request via async context managers; nothing is pooled across requests, so connection-pool bleed (the doc 05 edge case above) cannot occur. A per-organization pool is deferred until throughput demands it. |
| Per-organization model isolation | `app/agent/model_resolver.py` constructs the provider per request from settings; no cross-organization adapter state. Per-organization `OrganizationModelConfig` table (so organization A runs Claude while organization B runs Ollama) is a Phase 5+ enhancement — the `get_model_for_organization(ctx, ...)` signature already takes the organization context so the seam exists. |
| Continuous isolation testing (CI + synthetic probe) | **CI:** `tests/test_cross_organization_isolation.py` + `tests/test_organization_scoped_cache.py` run on every PR (named explicitly in `.github/workflows/ci.yml`). **Synthetic probe:** `tools/organization_isolation_test/` is a runnable CLI (6 live checks: RAG both directions, audit both directions, cache prefix-rejection, cache cross-organization) that operators run against a live DB. `make test-isolation` (unit) and `make test-isolation-live` (smoke). Phase 4 Slice 4. |

**Dev two-organization pattern.** Meaningful isolation testing needs two
organizations with distinct private content. The dev setup bootstraps `acme`
and `beta` (see ONBOARDING Gotcha #8), each seeded via
`app.management.seed_dev_knowledge --organization-slug <slug>` so each has
private runbook + past-incident chunks that visibly reference its own
name. `tools/organization_isolation_test` then proves neither can retrieve
the other's chunks.

**Still owed (post-Phase-4):** the per-organization secrets backend today is
the Fernet-encrypted file backend, not a real secrets manager (Vault /
OpenBao). The `SecretsBackend` protocol abstracts this — swapping in a
Vault backend is a backend implementation, not an application change —
but the production-grade manager itself is Phase 6+ deployment work.
