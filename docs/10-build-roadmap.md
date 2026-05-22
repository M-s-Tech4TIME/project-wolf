# 10 — Build Roadmap

This is a phased plan that builds the platform in the right order — earliest
phases prove the riskiest assumptions, latest phases add capabilities that are
optional or that benefit from the experience of running the earlier ones.

**This roadmap is the recommended order for the coding agent to implement.**

## Phase 0 — Foundations (week 0-1)

Things that must exist before anything else, none of them glamorous.

- Repo, license, CI skeleton (Apache 2.0, lint, type-check, test).
- `docker-compose.yml` that brings up Postgres, pgvector, and a minimal "hello
  world" orchestrator service.
- Auth scaffolding: a local-account login flow + an OIDC adapter (defer SSO
  configuration to the operator).
- The tenant data model (`tenants`, `users`, `user_tenants`, `roles`) and the
  immutable request-context construct.
- The secrets-backend interface, with a simple encrypted-file backend that works
  out of the box.
- Structured logging, OpenTelemetry tracing, audit-log skeleton.

**Exit criteria:** a developer can `make up`, log in, and the system records an
audit event for the login.

## Phase 1 — The model abstraction (week 1-2)

The single most important piece of risk to retire early, because the project's
promise depends on it.

- Define the `ModelProvider` interface and the capability descriptor.
- Implement at least three adapters: **Anthropic**, **OpenAI**, **Ollama**. (The
  others — Gemini, DeepSeek, generic OpenAI-compatible — can follow.)
- Implement structured-JSON-output fallback for adapters without reliable native
  tool-calling.
- Build the model-probe tool (`tools/model_probe`) — the self-test that grades a
  configured model and outputs its capability descriptor.
- Document, per known model, the empirically observed capability tier.

**Exit criteria:** a developer can configure any of the three providers, run the
probe, and the orchestrator picks the matching strategy (frontier/mid/basic).

## Phase 2 — The read path, end to end (week 2-4)

This delivers the first real user value and proves the agent loop works.

- Wazuh OpenSearch client with **forced tenant filter** in the query layer.
- Wazuh Server API client (read endpoints only).
- The tool registry with **strict input/output schemas** (Pydantic).
- The first read tools: `search_alerts`, `aggregate_alerts`,
  `get_event_timeline`, `get_agent_alert_history`, `list_agents`,
  `get_agent_detail`, `get_rule_definition`, `get_cluster_health`.
- The orchestrator's agent loop with the three strategies wired up
  (frontier/mid/basic).
- Resource guardrails enforced before tool execution (time-range caps, result
  caps, rate limits).
- Audit logging for every model call and tool call.
- A minimal UI: log in, pick a tenant, ask a question, see the answer with
  citations.

**Exit criteria:** an analyst can ask "why did agent X trigger alert Y at time
Z?" and receive a grounded, cited answer from data in a real Wazuh deployment.
Tested on a frontier model **and** a local Ollama model with the basic strategy.

## Phase 3 — The RAG / knowledge layer (week 4-5)

- Vector store interface; pgvector implementation.
- Ingestion pipeline: structure-aware chunking, metadata extraction.
- Seed the corpora: Wazuh docs (via `tools/seed_knowledge`), ATT&CK.
- Hybrid retrieval (vector + BM25).
- The `query_runbook` tool with metadata filters as first-class arguments.
- The grounding validator: rejects ungrounded factual claims in answers.
- Per-tenant private corpus partition (foundation laid for tenants' runbooks even
  though uploads come later).

**Exit criteria:** asking a question about Wazuh behavior produces an answer that
cites doc chunks. Asking about ATT&CK techniques produces an answer that cites
versioned ATT&CK content.

## Phase 4 — Multi-tenancy hardening (week 5-6)

Crucial before any MSSP-targeted feature.

- Tenant onboarding with connection validation and immutable profiles.
- Per-tenant credential storage in the secrets backend.
- Connection pooling per tenant (or stateless checkout-and-establish).
- Cache wrapper with mandatory tenant-prefix keys.
- **The cross-tenant test suite** (`tools/tenant_isolation_test`) running in CI.
- Audit-stream tenant scoping verified by the test suite.

**Exit criteria:** the isolation test suite passes for every read tool, every
RAG retrieval, every audit query, and every cache path. Two tenants can be
configured and operated side-by-side with verifiable separation.

## Phase 5 — Cases and reporting (week 6-8)

The "outmost sophistication" the project's motive calls for begins here.

- The case data model — triggering signal, timeline, findings, proposals,
  communications, disposition.
- Auto-case creation on serious investigations; manual case creation.
- Case UI: timeline view, findings view, evidence appendix.
- Report templates: incident, executive, compliance, shift handover, threat-hunt.
- Templated, slot-filled report generation with grounding validation.
- Export to Markdown, HTML, PDF.

**Exit criteria:** an analyst can take an investigation from question to closed
case, produce a grounded incident report, and the report opens cleanly in PDF.

## Phase 6 — Propose tools and the Approval Gateway (week 8-10)

The most safety-critical work in the project. Built last for read-side features
on purpose: the read-side platform must be solid before any state-changing path
is opened.

- The **separate gateway service** with its own credentials.
- The proposal data model and state machine.
- Propose tools: `propose_active_response`, `propose_rule_tuning`,
  `propose_agent_action`, `propose_config_change`.
- Approval authority model: tenant, action class + severity, target sensitivity.
- Crown-jewel tagging.
- The approval queue UI: show evidence, resolved target, rationale.
- Signed approval tokens bound to content hash.
- The execute tools — **only** inside the gateway, not in the orchestrator.
- Freshness re-check on `approved → executing`.
- Verification read after execution.
- Rollback path for reversible actions.
- Audit transitions for every proposal state change.
- Separation of duties: requester cannot approve.
- Four-eyes for critical-severity actions.

**Ship v1 with no auto-execution** (see `04`).

**Exit criteria:** an analyst can request an active response, a different
analyst with the right authority can approve it, the gateway executes it
against a real Wazuh deployment, and the verification read confirms the actual
state. Every step audited.

## Phase 7 — Detection engineering and threat-hunt features (week 10-12)

With the safety story in place, expand analyst capabilities.

- `propose_rule_tuning` enhanced: produces a diff with an explanation.
- A "rule explorer" UI surface that pairs `get_rule_definition` with the agent's
  explanation of what the rule does and the alerts it has fired.
- Threat-hunt mode: hypothesis-driven sessions, hunt reports.
- The `lookup_ioc` and `enrich_geoip` enrichment tools wired to configurable
  threat-intel sources.

**Exit criteria:** a detection engineer can investigate a noisy rule, propose a
tuning, and have it executed through the gateway.

## Phase 8 — Playbooks and orchestration (week 12-14)

- The playbook engine: named, versioned, step-by-step workflows with explicit
  checkpoints (`08`).
- A starter library of playbooks for common scenarios.
- The shift-handover report generated from open cases.
- Cross-case analytics dashboards (per-tenant and MSSP-parent-scope).

**Exit criteria:** a SOC can codify a common investigation flow as a playbook,
run it consistently across analysts, and the resulting cases are produced to the
same standard.

## Phase 9 — Knowledge feedback and growth (week 14-15)

- Case-close summary: analyst-reviewed, structured.
- Auto-ingest of reviewed summaries into the tenant's private corpus, with
  audit and reversibility.
- Operator controls over what auto-ingests and from where.
- Periodic re-evaluation of retrieval quality.

**Exit criteria:** after one month of use, a tenant's agent answers tenant-
specific questions noticeably better than on day one, traceable to ingested
case summaries.

## Phase 10 — Integrations (ongoing)

- Notification adapters: Slack, Teams, email, webhook.
- Ticketing adapters: Jira, ServiceNow, webhook.
- Audit-log forwarding to external SIEM (including back to Wazuh as a separate
  index).

These can land anytime after Phase 5; they are not gating.

## Phase 11 — Optional auto-execution (only after sustained operational history)

Only consider this after the platform has months of safe operation and the team
has data showing the agent's proposals are consistently sound.

Conditions defined in `04`. Default off, opted-in per tenant, narrowly scoped,
circuit-broken, fully audited.

## Things to deliberately defer (or never build)

- **Autonomous action without human approval** — the foundational scope decision.
- **Log mutation** — never a capability, not even as a proposal.
- **Replacing Wazuh** — Wolf augments, never replaces.
- **General-purpose chat** — keep the agent scoped to security operations.
- **A model marketplace, billing, usage metering** — the platform is free and
  open-source; cost transparency yes, metering no.

## Quality gates that apply to **every** phase

These are non-negotiable; the coding agent must enforce them at all times.

1. **Strict typed schemas** on every tool input and output.
2. **Tenant context injected by the orchestrator**, never read from the model.
3. **Capability tiers enforced** by the registry and dispatch logic.
4. **Audit every event** that matters.
5. **No execute tool in the orchestrator process**; they exist only in the
   gateway.
6. **The cross-tenant isolation test suite must pass** in CI for any change to
   touch main.
7. **Grounding validator runs** on any final answer that makes factual claims.
8. **Every model call works on at least one local model** in CI — proving the
   "no paid dependency required" promise stays true throughout.
