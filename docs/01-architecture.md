# 01 — System Architecture

## Architectural stance

Wolf is a **separate platform** that connects to a Wazuh deployment from the
outside. It never runs inside Wazuh, never shares Wazuh's process space, and a
failure in Wolf must never affect detection. Wazuh remains the authoritative
system of record; Wolf reasons over its data.

## The layers

The system is built as a stack of layers. Each has one responsibility and a clear
boundary with its neighbors.

```
+-----------------------------------------------------------+
|  User & Identity layer                                    |
|  - Authentication (SSO / OIDC), users, roles               |
|  - Organization binding: a session is bound to one organization        |
+-----------------------------------------------------------+
                          |
+-----------------------------------------------------------+
|  Tenancy layer                                             |
|  - Organization registry: connection profiles, scoped creds      |
|  - Establishes the immutable organization context per request    |
+-----------------------------------------------------------+
                          |
+-----------------------------------------------------------+
|  Agent Orchestrator (the core service)                     |
|  - Runs the agent loop: plan -> call tools -> observe       |
|  - Owns the tool catalog and capability-tier dispatch       |
|  - Injects organization context into every tool call             |
|  - Talks to the LLM via the Model Abstraction layer         |
+-----------------------------------------------------------+
        |                    |                    |
+---------------+   +------------------+   +-----------------+
| Model         |   | Tool / Capability|   | Knowledge / RAG |
| Abstraction   |   | layer            |   | layer           |
| (any LLM)     |   | read/propose/exec|   | live + stable   |
+---------------+   +------------------+   +-----------------+
                          |
        +-----------------+------------------+
        |                                    |
+-------------------+              +---------------------+
| Read tier         |              | Action tier         |
| OpenSearch indexer|              | Wazuh Server API     |
| READ-ONLY creds   |              | read freely,         |
|                   |              | changes gated        |
+-------------------+              +---------------------+
                                             |
                                   +---------------------+
                                   | Approval & Action   |
                                   | Gateway             |
                                   | human approves,     |
                                   | gateway executes    |
                                   +---------------------+

+-----------------------------------------------------------+
|  Audit & Provenance layer  (cross-cutting, append-only)    |
+-----------------------------------------------------------+
```

## Component responsibilities

### User & Identity layer

Authenticates users (prefer OIDC / SSO; support local accounts for self-hosted
simplicity). Each user has one or more **roles** and is bound to one or more
**organizations**. A session, once established, is bound to exactly one active organization. The
AI never selects the organization — see `05-multi-organization.md`.

In the Next.js 16 frontend, the **network-boundary entry point is `proxy.ts`**
(the successor to `middleware.ts`), which runs before every request. This is
where session validity is checked, the user identity is resolved, and the
organization context is bound to the request before any page, Server Action, or
internal API route executes. Organization context flows from `proxy.ts` outward; no
downstream code is permitted to derive tenancy from anywhere else.

### Tenancy layer

Holds the **organization registry**. Each organization record contains:

- A connection profile to that organization's Wazuh Indexer (OpenSearch endpoint).
- A connection profile to that organization's Wazuh Server API.
- **Scoped credentials** for both, stored in a secrets manager, never in plaintext
  config.
- The organization's RAG partition identifier.
- The organization's policy settings (approval levels, auto-execute opt-ins, rate limits).

It produces the **immutable organization context** that is stamped onto every request and
carried, unmodifiable, through every downstream layer.

### Agent Orchestrator

The core service and the "brain wiring." It does **not** contain intelligence — the
LLM does — but it controls everything around the LLM:

- Receives an analyst request with its organization context.
- Runs the **agent loop**: send state to the model, receive a tool call or an
  answer, dispatch the tool call, feed the result back, repeat until done.
- Owns the **tool catalog** and enforces **capability-tier dispatch** (read tools
  auto-run; propose tools emit a proposal; execute tools are not reachable by the
  model at all — see `03`).
- Injects organization context into every tool call. The model's output is untrusted for
  tenancy.
- Holds **all conversation and investigation state**. The LLM is stateless; the
  orchestrator is the memory.
- Talks to the model only through the **Model Abstraction layer**, so the underlying
  model is swappable.
- Applies the **agent strategy** appropriate to the current model's capability tier
  (see `02`).

### Model Abstraction layer

Presents one internal interface to the orchestrator and adapts it to whichever model
backend is configured — Claude, OpenAI, Gemini, DeepSeek, Ollama, or any
OpenAI-API-compatible endpoint. Normalizes tool-calling, streaming, token accounting,
and error handling. Carries a **capability descriptor** for the active model that
tells the orchestrator how much autonomy to grant. Fully detailed in `02`.

### Tool / Capability layer

The concrete tools the agent can use, each tagged with exactly one capability tier
(`read`, `propose`, `execute`). This layer also enforces **resource guardrails**
(query cost, result caps, rate limits) which are orthogonal to the capability tier.
Fully detailed in `03`.

### Read tier — OpenSearch Indexer

The platform's read surface. All alert, event, and historical data is queried here.
The credential used for this tier is an OpenSearch role that **physically lacks
write and delete privileges** on alert indices. This is how "the AI cannot alter or
delete a log" becomes a structural fact.

### Action tier — Wazuh Server API

Used **read-only** for most operations (listing agents, fetching rules and decoders,
cluster health, configuration introspection). The dangerous subset — active
response, agent restarts, configuration changes — is reachable only through the
Approval & Action Gateway, never directly by the agent.

### Approval & Action Gateway

The only component that executes state-changing actions. It takes a human-approved
proposal, validates the signed approval token, performs a freshness re-check,
executes against the Wazuh Server API, verifies the actual end state, and records
everything. Fully detailed in `04`.

### Knowledge / RAG layer

Splits knowledge into **live state** (fetched fresh through read tools, never
cached) and **stable knowledge** (Wazuh docs, ATT&CK, the organization's runbooks and past
incidents — retrieved through a partitioned vector store). Fully detailed in `06`.

### Audit & Provenance layer

Cross-cutting. Every model call, tool call, proposal, state transition, approval,
and execution is written here as an immutable, organization-tagged record. The audit store
is append-only and outside the write reach of the agent and the gateway, so nothing
in the system can rewrite its own history.

## The two trust tiers — the central idea

Wazuh exposes two interfaces, and they are treated as **fundamentally different
trust tiers**:

| | Read tier (OpenSearch) | Action tier (Server API) |
|---|---|---|
| Primary use | Querying data | Fleet introspection + actions |
| Default access | Read-only, used constantly | Read-only by default |
| Credential | Physically lacks write/delete | Scoped; powerful subset gated |
| Risk | Resource exhaustion, data volume | Real-world endpoint changes |
| Agent autonomy | Auto-runs read tools | Cannot execute; only propose |

Keeping these as two separate credential sets, two separate tool groups, and two
separate audit categories pays off across the whole system.

## Data flow — two canonical journeys

### Journey A — an investigation (read-only, ~90% of usage)

1. Analyst asks: "Why did agent web-07 trigger a brute-force alert at 02:00?"
2. Orchestrator stamps the organization context, starts the agent loop.
3. The model plans and calls read tools: `search_alerts`, `get_event_timeline`,
   `get_agent_status`, `get_rule_definition`.
4. The orchestrator dispatches each, injecting organization context, enforcing resource
   guardrails, returning results to the model.
5. The model calls `query_runbook` (RAG) for relevant docs and the organization's
   procedures.
6. The model composes an evidence-grounded answer; every claim cites a tool result
   or a retrieved chunk.
7. Every step is written to the audit log. Nothing changed anywhere.

### Journey B — a response (state-changing)

1. Analyst says: "Isolate web-07."
2. The orchestrator does **not** execute. The model calls
   `propose_active_response`.
3. A structured **proposal object** is produced — resolved target, exact command,
   rationale, evidence alert IDs, expected effect, rollback plan, content hash — and
   placed in the approval queue.
4. A human with the required authority for this action class and target reviews it.
5. On approval, a signed approval token bound to the proposal's content hash is
   minted.
6. The **gateway** (not the agent, not the model) takes the token, performs a
   freshness re-check, executes against the Server API, verifies the real end state.
7. The full provenance — who asked, what was proposed, who approved, what happened —
   is written to the audit log.

The separation in Journey B is the entire safety story: the agent can *think about*
anything but can only *do* what a human signed off on.

## Deployment shape

Wolf is containerized and self-hostable end to end. A reference deployment:

- The Orchestrator service (stateless app tier, horizontally scalable).
- The Approval Gateway service (separate service, separate credentials).
- A relational database for organizations, users, proposals, cases, audit.
- A vector store for the RAG layer.
- A secrets manager for per-organization credentials.
- An optional bundled model runtime (Ollama) for fully-free, fully-local operation.

All components must be runnable on a single machine for a small single-org
deployment, and separable for scale. No component may require a paid external
service to function.
