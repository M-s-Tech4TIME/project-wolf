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
