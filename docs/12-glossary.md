# 12 — Glossary

Definitions used throughout this bundle.

**Action class** — A category of state-changing action: `active_response`,
`rule_tuning`, `agent_action`, `config_change`. Each has a baseline severity.

**Agent** — Overloaded term in this project. Disambiguate by context:
- *Wazuh agent* — the endpoint software collecting events for Wazuh.
- *AI agent* — the LLM-driven loop running inside the orchestrator.
When ambiguous in code or docs, prefix: `wazuh_agent` vs `ai_agent`.

**Approval token** — A signed credential, bound to a proposal's content hash,
that the gateway requires before executing the action. Issued only by the
approval service on a human action.

**Audit log** — The append-only, tenant-tagged record of every event in the
system. Outside the write reach of the orchestrator and gateway. Authoritative
for "what happened."

**Capability descriptor** — A model's measured profile: context window, tool-
calling reliability, reasoning tier, etc. Tells the orchestrator which agent
strategy to apply.

**Capability tier** — The classification of a tool: `read`, `propose`, or
`execute`. A property of the tool, fixed at registration.

**Case** — The unit of orchestration: a structured record of an investigation
or incident response. Tenant-scoped. The analyst's view of an ongoing or closed
piece of work.

**Content hash** — A hash over a proposal's immutable fields. The human approves
this hash; the gateway executes this hash. Any drift aborts.

**Crown-jewel asset** — An asset tagged sensitive by the operator (domain
controllers, Wazuh managers, payment systems). Actions against tagged assets
escalate to the highest required approval level regardless of action class.

**Execute tool** — A tool that actually performs a state-changing action.
Present only in the gateway service. Not in the model's tool schema. Callable
only with a valid approval token.

**Four-eyes** — A separation-of-duties control requiring two distinct approvers
for critical-severity actions.

**Freshness re-check** — A re-query of evidence and target state performed by
the gateway on `approved → executing`. If the world has moved since approval, the
gateway refuses to execute and returns the proposal for re-review.

**Frontier / strong / mid / basic** — The four model reasoning tiers used by
the orchestrator's strategy selector. See `02-model-abstraction.md`.

**Gateway** — Short for *Approval & Action Gateway.* The separate service that
holds the execute tools and runs the proposal lifecycle.

**Grounding validator** — A check that confirms every factual claim in an
agent's answer traces to a real tool result or a real retrieved chunk. Ungrounded
claims are removed or flagged.

**Indexer (Wazuh)** — Wazuh's OpenSearch-backed data store of alerts and events.
The read tier of the platform.

**MSSP parent scope** — A logical scope above tenants for MSSPs that own multiple
tenants. Cross-tenant queries are permitted only within an MSSP's owned set, with
the set resolved server-side from the account.

**Orchestrator** — The core service that runs the agent loop and dispatches tool
calls. Holds conversation state. Communicates with the LLM via the model
abstraction layer.

**Playbook** — A named, versioned sequence of steps with explicit checkpoints
for a common scenario. The deterministic frame that lets even weaker models run
complex investigations reliably.

**Proposal** — A structured, typed object emitted by a propose tool. Carries
target, action, parameters, rationale, evidence, expected effect, rollback plan,
severity, and content hash. The contract between the agent and the gateway.

**Propose tool** — A tool that produces a proposal and executes nothing. Safely
callable by the agent.

**Read tool** — A tool that queries data and changes nothing. Auto-runs when the
agent calls it.

**Wolf** — The name of this project. The platform's agentic AI layer for
Wazuh. Pack-aware, hunts in coordination, recognizes threats.

**Server API (Wazuh)** — Wazuh's management API. Used read-only directly by the
agent; state-changing endpoints are reached only by the gateway.

**Strategy** — How the orchestrator runs the agent loop, selected by the model's
reasoning tier. The strategies are: autonomous multi-step (frontier/strong),
guided with checkpoints (mid), deterministic pipeline with model-in-the-slots
(basic).

**Target sensitivity** — One of the three axes of approval authority. Crown-
jewel assets escalate the required level.

**Tenancy / tenant** — A tenant is one isolated customer of the platform. A
single-org deployment has exactly one tenant. An MSSP has many. Every piece of
code threads tenant context; there is no untenanted code path.

**Tenant context** — The immutable per-request data carrying the active tenant
ID. Stamped by the orchestrator from the authenticated session. Injected into
every tool call. The model cannot influence it.

**Trust tier** — The two-way distinction between the **read tier** (OpenSearch
Indexer, read-only) and the **action tier** (Server API, read freely but
state-changes gated). They are kept architecturally separate throughout the
platform.

**Verification read** — A read tool call run by the gateway after any execute
call to determine the actual end state. The gateway records the verified state,
not the optimistic API return value.
