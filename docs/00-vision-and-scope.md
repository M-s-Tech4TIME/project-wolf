# 00 — Vision and Scope

## The motive

Security operations teams running Wazuh face a structural problem: the volume of
alerts and events far exceeds the human capacity to triage, investigate, and
document them with consistent quality. Analysts spend their time writing the same
OpenSearch queries, reconstructing the same timelines, looking up the same rule
definitions, and writing reports by hand. Context is lost between shifts. Junior
analysts lack the experience of senior ones. Investigations are inconsistent.

Wazuh is unusually well suited to an AI layer because its data is **open**: the
Indexer is OpenSearch, queryable directly; the Server API is documented and
complete. Unlike closed commercial SIEMs, nothing has to be reverse-engineered.

**The motive of this project** is to give every analyst — regardless of seniority —
a tireless, consistent, well-documented assistant that can:

- **Detect** — surface and correlate what matters out of the noise.
- **Analyze** — investigate an alert or incident with the rigor of a senior analyst,
  composing the queries, reconstructing timelines, and explaining causes.
- **Respond** — propose precise, safe response actions for a human to approve.
- **Report** — produce incident reports, executive summaries, and compliance
  evidence with sophistication, accuracy, and consistency.
- **Document and orchestrate** — turn every investigation into a permanent,
  searchable case record, so knowledge compounds instead of evaporating.

The goal is **absolute precision** in detection, analysis, and response, and
**outmost sophistication** in reporting, documentation, and orchestration — the
analyst's words, and the design target of this project.

## What success looks like

- An analyst asks a plain-language question about an alert and receives an accurate,
  evidence-grounded answer with every claim traceable to a real Wazuh document or
  tool result.
- A response action is never taken without a human seeing exactly what will happen,
  why, and to which asset — and approving it.
- A logged-out analyst returning to a case sees a complete, structured record of
  what was investigated and concluded.
- An MSSP runs many clients on one deployment with zero risk of cross-client data
  exposure.
- A team running a free local model gets a platform that is still robust, safe, and
  useful — with the agent's autonomy scaled honestly to the model's capability.

## In scope

- Reading and querying the Wazuh Indexer (OpenSearch).
- Reading the Wazuh Server API: agents, rules, decoders, cluster health,
  configuration introspection, SCA results.
- Alert triage, correlation, and incident narrative construction.
- Investigation assistance: timeline reconstruction, root-cause explanation.
- Detection engineering assistance: explaining, drafting, and testing rules and
  decoders (as proposals).
- Threat-intel enrichment and MITRE ATT&CK mapping.
- Report generation: incident, executive, compliance.
- Case management and documentation.
- **Proposing** active response and configuration changes for human approval.
- A human-in-the-loop approval gateway that **executes** approved actions.
- Multi-organization for MSSP and single-org use.
- A model-agnostic LLM layer supporting hosted and local models.

## Out of scope (deliberately)

- **Autonomous response without human approval** — not in v1, possibly never; see
  `04-approval-gateway.md` for the strict conditions any future auto-execution must
  meet.
- **Altering or deleting logs** — never. Not a feature, not a proposal type. The
  platform has no capability to mutate evidentiary data.
- **Replacing Wazuh components** — Wolf augments Wazuh; it does not reimplement
  the indexer, server, or dashboard.
- **Being the system of record for detections** — Wazuh remains authoritative.
  Wolf reasons over Wazuh's data; it does not own it.
- **General-purpose chat** — Wolf is a security operations tool, scoped to that.

## Design tenets that shape every later document

1. **The blast radius of the AI is bounded by architecture.** If every prompt
   instruction were ignored or subverted, the system would still be safe, because
   credentials and tool schemas — not instructions — enforce the limits.
2. **Untrusted input is everywhere.** Log lines, filenames, process arguments, and
   ingested past reports all contain attacker-controllable text. All of it is
   treated as data to reason over, never as instructions to follow.
3. **The human is the decision-maker for anything that changes the world.** The AI
   accelerates and informs; it does not decide to act.
4. **Precision is a property of the system, not the model.** Strict schemas,
   validation, grounding, and approval make answers and actions trustworthy. The
   model determines how much the system can take on autonomously, not whether it can
   be trusted.
5. **Open-source and self-hostable end to end.** No required external dependency
   that costs money or imposes limits. A team must be able to run the entire
   platform — including the model — on their own hardware, for free.
