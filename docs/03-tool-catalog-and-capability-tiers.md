# 03 — Tool Catalog and Capability Tiers

This is the spine of the platform. Every capability the agent has is a **tool**, and
every tool has exactly one **capability tier**. The tier system is what makes the
platform's safety guarantees structural rather than aspirational.

## The capability model

Every tool is assigned one tier at registration time. The tier is a fixed property
of the tool, defined in code, never negotiable by the model.

### `read`

Queries data, changes nothing. **Auto-runs** the moment the model calls it — no
human in the loop. Most of the platform's work happens here.

### `propose`

Produces a structured, reviewable **proposal object** describing a state-changing
action. It **changes nothing itself.** Its entire output is data placed in the
approval queue. The model may call propose tools as freely as read tools — a
proposal is just data.

### `execute`

Actually changes endpoint or server state. **The model has no ability to call these
at all.** They are not present in the tool schema sent to the model. They are
invoked only by the Approval Gateway, only after a human approves a matching
proposal. See `04`.

There is deliberately **no tier** that lets the model both decide on and perform a
state change. That gap is the safety guarantee.

## Why the execute boundary actually holds — four structural facts

The boundary is not a prompt instruction (an attacker can subvert those via injected
log content). It rests on four independent structural facts:

1. **Execute tools are absent from the model's tool schema.** When the orchestrator
   builds the tool list for a model call, it filters to `tier IN (read, propose)`.
   The model cannot emit a call to a tool it was never given.
2. **Dispatch is an allowlist, not a denylist.** When a tool call returns from the
   model, the orchestrator looks it up in the registry. `read` runs; `propose` emits
   a proposal; anything else — unknown tool, or an execute tool — is rejected and
   logged as an anomaly. No code path takes a model-originated call and runs an
   execute tool.
3. **Credentials enforce it independently.** Even if facts 1 and 2 both failed, the
   OpenSearch role used by the read tier physically lacks write/delete on alert
   indices, and the Server API credential used directly by the agent lacks the
   permissions for state-changing endpoints. The data layer itself refuses.
4. **The gateway requires a signed approval token.** An execute tool demands a token
   proving a specific human approved a specific proposal (bound to the proposal's
   content hash). The orchestrator cannot mint these; only the approval service can,
   and only on a human action.

Four independent failures would have to align for the boundary to break. That is
what makes "the AI cannot alter or delete a log, or change config on its own" a true
statement about the system.

## Resource guardrails — orthogonal to tiers

The capability tier governs **what kind of effect** a tool has. It does **not**
govern **blast radius.** A `read` tool is safe in the "changes nothing" sense but a
broad raw-log query can still exhaust the indexer or pull huge volumes of PII into
context. Therefore every tool — read tools included — is also subject to **resource
guardrails**, a second, orthogonal layer:

- Maximum time range per query.
- Result-count caps and mandatory pagination.
- Query-cost limits (reject queries estimated to be too expensive).
- Per-tenant rate limiting.
- Context-volume limits (cap how much retrieved data enters the model context).

Resource guardrails are enforced by the tool layer before any tool executes.

## The tool catalog

Each tool below has, in the real implementation, a strict input schema and a strict
output schema (define these as typed contracts — e.g. JSON Schema or typed models).
Malformed calls are rejected, never guessed at.

### Read tools — OpenSearch Indexer tier

Credential: OpenSearch role, physically read-only on alert indices.

| Tool | Purpose |
|------|---------|
| `search_alerts` | Query alerts by time range, agent, rule ID, level, ATT&CK technique, free text. Paginated. |
| `aggregate_alerts` | Bucketed counts/stats over a query — alerts per agent, per rule, per hour — for triage and trend views. |
| `get_event_timeline` | Ordered sequence of events for a host or entity across a window. The backbone of investigation. |
| `get_agent_alert_history` | Alert history for one agent. |
| `search_raw_logs` | Query archived raw events where indexed. Heavier; strict resource guardrails. |
| `get_vulnerability_findings` | Vulnerability detector results for an agent or fleet. |

### Read tools — Wazuh Server API tier

Used read-only. Credential scoped to introspection endpoints only.

| Tool | Purpose |
|------|---------|
| `list_agents` | Fleet inventory: status, OS, group, last-seen. |
| `get_agent_detail` | Deep detail on one agent, including sync state. |
| `get_rule_definition` | Full rule definition and metadata for a rule ID — lets the agent explain *why* an alert fired. |
| `get_decoder` | Decoder definition. |
| `get_cluster_health` | Manager/cluster node status, indexer health. |
| `get_active_config` | The currently running configuration of a component (read-only introspection). |
| `get_sca_results` | Security Configuration Assessment / compliance check results. |
| `list_active_response_commands` | The active-response commands actually configured and deployed on the manager — so proposals can only reference real, available commands. |

### Propose tools

Each returns a proposal object and executes nothing. See `04` for the proposal
schema and lifecycle.

| Tool | Purpose |
|------|---------|
| `propose_active_response` | Proposes running a configured active-response command (isolate host, block IP, kill process, etc.) against a resolved target agent. |
| `propose_rule_tuning` | Proposes a rule/decoder change to fix a false positive. Output includes a diff and rationale. |
| `propose_agent_action` | Proposes restart / upgrade / group reassignment of an agent. |
| `propose_config_change` | Proposes a configuration change to a manager or agent group. Output includes a diff. |

**There is deliberately no propose tool that touches logs.** Log mutation is not a
capability the platform has — not as an action, not even as a proposal. This
enforces the vision's commitment that the AI can never alter or delete a log.

### Enrichment tools (read-only)

| Tool | Purpose |
|------|---------|
| `lookup_ioc` | Check an IP / hash / domain against configured threat-intel feeds. |
| `map_to_mitre` | Map an alert or incident to MITRE ATT&CK tactics and techniques. |
| `query_runbook` | RAG retrieval over Wazuh docs and the tenant's own runbooks and past incidents. |
| `enrich_geoip` | Geolocation / ASN for an address. |

### Execute tools — gateway-only, NOT in the model's schema

| Tool | Counterpart of |
|------|----------------|
| `execute_active_response` | `propose_active_response` |
| `apply_rule_tuning` | `propose_rule_tuning` |
| `execute_agent_action` | `propose_agent_action` |
| `apply_config_change` | `propose_config_change` |

The model cannot name these, call these, or see these. They exist only inside the
Approval Gateway.

## Tool design rules (for the implementer)

1. **Strict schemas both ways.** Every tool declares a typed input schema and a
   typed output schema. The orchestrator validates the model's call against the
   input schema before dispatch and validates the tool's result against the output
   schema before returning it to the model.
2. **No free-text where structure is possible.** Tools return structured data, not
   prose. Structured output resists prompt injection (an attacker can put text in a
   log message field but cannot restructure the tool's typed result) and is reliable
   for weaker models to consume.
3. **Tenant context is injected, never accepted from the model.** Every tool
   execution receives the tenant context from the orchestrator. If the model's tool
   call somehow includes a tenant identifier, it is ignored. See `05`.
4. **Targets are resolved, not guessed.** Propose tools must receive a resolved,
   unambiguous agent ID — produced by an earlier read step — never a human-readable
   name the model guessed. If resolution is ambiguous, the propose tool fails and
   asks for disambiguation. See `04`.
5. **Tools reference only real, deployed capabilities.** `propose_active_response`
   may only reference commands returned by `list_active_response_commands`. The agent
   cannot invent an action.
6. **Every tool call is audited.** Tool name, tier, tenant, inputs (sensitive values
   redacted), result summary, timing, and outcome are written to the audit log.
7. **Tools fail cleanly.** A tool that cannot complete returns a structured error.
   It never returns a fabricated success and never silently returns partial data as
   if complete.
8. **The tool catalog is a registry.** Tools are registered with their tier, schemas,
   resource guardrails, and required role. Adding a tool is adding a registry entry.
   The registry is the single source of truth the orchestrator consults.

## The dispatch flow (pseudocode)

```
on model_tool_call(call):
    tool = registry.lookup(call.name)
    if tool is None:
        audit.anomaly("unknown tool", call); reject()
    if tool.tier == EXECUTE:
        audit.anomaly("model attempted execute tool", call); reject()
    validate(call.arguments, tool.input_schema) or reject()
    enforce_resource_guardrails(tool, call, tenant_context) or reject()
    inject tenant_context into the execution context  # not from the model
    if tool.tier == READ:
        result = tool.run(call.arguments, tenant_context)
        validate(result, tool.output_schema)
        audit.tool_call(...)
        return result_to_model(result)
    if tool.tier == PROPOSE:
        proposal = tool.run(call.arguments, tenant_context)  # builds proposal object
        proposal_queue.enqueue(proposal)
        audit.proposal_created(...)
        return proposal_receipt_to_model(proposal)   # model is told a proposal was filed
```

Note there is no branch that executes an execute tool from a model call. That branch
does not exist anywhere in the orchestrator.
