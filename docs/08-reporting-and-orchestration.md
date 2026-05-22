# 08 — Reporting, Documentation, and Orchestration

This is the "outmost sophistication" the project's motive calls for. A SOC drowns
in events; what makes the difference is **structured, accurate, repeatable
documentation** that turns every investigation into a record the team learns from.

## Cases — the unit of orchestration

The orchestration primitive is the **case**. A case is a structured record of an
investigation or incident response. It is created automatically when an analyst
starts a serious investigation (or is opened explicitly) and accumulates:

- **The triggering signal** — alert ID(s), the analyst's question, or the
  externally-reported event.
- **The investigation timeline** — every tool call the agent made, in order, with
  inputs, structured results, and timing.
- **Findings** — what the agent and analyst concluded, with citations to evidence
  (alert IDs, retrieved runbook chunks, ATT&CK mappings).
- **Proposals** — every action proposed, its lifecycle, who approved or rejected,
  the verification result.
- **Communications** — notes added by the analyst, hand-offs between shifts.
- **Final disposition** — closed-resolved, closed-false-positive, escalated, etc.
- **Lessons learned** — optional analyst-written summary that, on close, can
  auto-ingest into the tenant's private knowledge corpus (see `06`) so the next
  similar case benefits.

The case is **tenant-scoped** like everything else. It is the central object the
analyst sees in the UI; the agent is a participant in the case, not the owner.

## Report types

The platform produces several reliable report types. All reports are **grounded** —
every claim must trace to a tool result or a retrieved chunk; ungrounded statements
are caught by the grounding validator (`06`).

### Incident report

Audience: technical responders, post-incident review.

Standard sections:
- Executive summary (2-3 sentences).
- Timeline of events (drawn from the case timeline and indexer queries).
- Affected assets (resolved agent IDs and identifying detail).
- Indicators observed (IPs, hashes, files, processes).
- MITRE ATT&CK mapping with versioned techniques.
- Detection coverage analysis — which Wazuh rules fired, which didn't, gaps.
- Response actions taken — every approved and executed proposal, with verification
  results.
- Recommendations — proposed rule tunings, configuration changes, runbook updates.
- Evidence appendix — alert IDs and other artifacts the report cites.

### Executive summary

Audience: leadership.

Plain-language, brief. What happened, what was affected, what was done, what is
the residual risk, what needs leadership decisions.

### Compliance evidence

Audience: auditors.

Wazuh already supports compliance tagging (PCI-DSS, HIPAA, GDPR, NIST, etc.). The
platform pulls relevant evidence by tag and assembles it into the format requested.
This is one of the highest-leverage use cases — analysts spend enormous time on
audit prep.

### Shift handover

Audience: the next shift.

Open cases, their state, what was tried, what's pending approval, what is waiting
on external input. Generated on demand.

### Threat-hunt report

Audience: detection engineers.

When an analyst (or the agent under instruction) runs a hypothesis-driven hunt,
this captures the hypothesis, the queries run, the findings, and recommendations
for new detections.

## How reports are produced

A report is **not** a free-text generation. It is a **templated, slot-filled
document** where each slot is populated by either a tool result, a retrieved
chunk, or a model-written paragraph that is itself grounded in cited evidence.

The template defines the structure. The agent populates the slots. The grounding
validator confirms every claim has a source. The final document is rendered to
Markdown, HTML, and PDF.

This is exactly the kind of task that works well across **all model tiers**: even
a basic local model can reliably summarize a fixed set of structured evidence into
a paragraph, because the planning and evidence-gathering were done by the
deterministic pipeline. The model fills slots; the platform guarantees the report
is well-structured and grounded.

## Documentation discipline

For each case, the platform automatically maintains:

- A **machine-readable record** (the canonical case object) — used for analytics,
  search, and re-ingestion into the knowledge corpus.
- A **human-readable narrative** that updates as the case evolves — the analyst's
  view.
- An **immutable audit trail** of every action (separate from the editable
  narrative).

This split matters: analysts must be able to add notes, correct misunderstandings,
and refine the narrative, **without** ever touching the audit trail. The audit
trail records what happened; the narrative records what the team understands. They
serve different purposes.

## Orchestration — multi-step workflows

Beyond single investigations, the platform supports **playbooks** — named,
versioned sequences of steps for common scenarios. A playbook is **not** an
opaque autonomy escalation; it is a structured workflow with explicit checkpoints.

Example: "Suspected brute-force on a public-facing host."

- Step 1 (auto): gather alert history for the host and source IP.
- Step 2 (auto): check the IP against threat-intel.
- Step 3 (auto): pull the host's authentication events.
- Step 4 (auto): map to ATT&CK and identify the relevant rules.
- Step 5 (auto): retrieve the tenant's runbook for this scenario.
- Step 6 (analyst checkpoint): present the synthesis; analyst confirms direction.
- Step 7 (propose): produce response proposals (block IP, increase logging,
  tighten rule).
- Step 8 (approval): proposals go through the gateway as normal.
- Step 9 (auto on completion): update the case, generate the report.

The playbook is the **deterministic frame** that lets a weaker model run a
complex investigation reliably — and gives a stronger model a known-good scaffold
to operate within when consistency matters more than creativity. See
`02-model-abstraction.md` for the strategy mapping.

## Cross-case analytics

The platform should expose, **per tenant**, analytics over closed cases:

- Mean time to triage, to first response, to closure.
- Most-frequent rule IDs in true positives vs false positives (a detection-tuning
  signal).
- Common ATT&CK techniques observed.
- Approval-queue latency.
- Auto-execution policy hit rates (when enabled).

For MSSPs, the **parent-scope** view (`05`) aggregates these across the MSSP's
owned tenants, still tagged per tenant.

## Knowledge feedback loop

On case close, the platform offers to ingest the case's structured summary into
the tenant's private RAG corpus (`06`). This is gated:

- The analyst can edit the summary before ingestion.
- The raw evidence is **not** auto-ingested (per the poisoning concern in `06`);
  only the analyst-reviewed structured summary is.
- Ingestion is auditable and reversible (the chunk can be removed).

Over time, this turns the platform into something that *knows the tenant's
environment* and improves with use. This is the most important feature for long-
term value, and the discipline around what auto-ingests is what keeps it safe.

## Integrations

The platform should support outbound integrations through a small set of
**adapters** rather than a sprawling integration surface:

- **Notification:** Slack, Teams, email, webhook.
- **Ticketing:** Jira, ServiceNow, generic webhook.
- **Long-term storage / SIEM forwarding:** the audit log and case records can be
  forwarded to an external store; even back to Wazuh as a separate index.

Each adapter is **read or write**, never granted execute authority on Wazuh. The
ticketing integration creates tickets when a case is opened; it does not authorize
actions.
