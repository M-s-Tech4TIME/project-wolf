# ADR 0017 — Wolf Central Brain: memory, deep-thinking, continuous learning

**Status:** ACCEPTED (2026-06-11)
**Authors:** Wolf Maintainers
**Extends:** ADR 0001 (model abstraction), ADR 0013 (grounding validator),
ADR 0014 (multi-embedding RAG), ADR 0015 (yellow vs red grounding), ADR 0016
(component architecture)
**Related:** ADR 0018 (Bootstrap Superuser + Per-Org RBAC + Login UX),
ADR 0019 (Web-first configurability mandate), ADR 0020 (Superuser-owned
Wazuh component mapping — install + per-org topology)
**Supersedes:** None — additive
**Revision history**:
- 2026-06-10 v1: initial draft (used "tenant" + "operator_id"); proposed
  separate ADR 0021 for Organizations.
- 2026-06-10 v2: aligned language to "organization" + "user_id" per
  operator direction. Dropped the ADR 0021 idea (tenant was already the
  org concept; no separate entity needed). Added explicit §"MSSP scenario
  worked example" and §"Wazuh access in MSSP" sections. Strengthened the
  per-organization isolation commitment to a load-bearing top-level section.
- 2026-06-10 v3: Wazuh component mapping split out from ADR 0018 into
  its own ADR 0020 (Related-list updated; §"Wazuh access in MSSP"
  forward-ref now points to 0020 not 0018).
- 2026-06-10 v4 (Round 1 of 4 of the operator review): added cross-
  reference to ADR 0019 "My memory: cross-org, self-only" subsection;
  clarified storage-vs-UI semantics so a reader of 0017 alone doesn't
  mis-interpret per-org partitioning as forbidding cross-org self-views.
- 2026-06-11 v5 (Round 2 of 4): Memory architecture finalized — 4
  layers (episodic/session/long-term/semantic); fact_type enum expanded
  to 6 categories (added incident_lesson; renamed relationship →
  social_context); confidence decay specified (exponential half-life,
  30d default, auto-prune < 0.1); retrieval timing locked (load-once
  at conversation start); semantic memory in Postgres tables (Neo4j /
  Memgraph rejected for v1); 4 memory open decisions RESOLVED
  (retention policy, opt-in vs always-on, cross-org boundaries,
  inspection UI scope).
- 2026-06-11 v6 (Round 3 of 4): Thinking layer + Self-validation
  layer + point-8 robust-answer-posture all settled. Deep-think
  trigger: both operator-explicit + auto-escalate. Cost cap: soft
  with warning. Action validator: hard gate, no bypass, no cost
  cap, inline rejection + edit-and-retry. Confidence calibration:
  3 states. Point 8 (the gating decision for the whole ADR):
  §"Robust answer posture" ACCEPTED as written — Wolf delivers
  rows 1-2 of the operator-experience contract; rejects rows 3-4
  to avoid hallucination during incident response.
- 2026-06-11 **ACCEPTED** (Round 4 of 4): Continuous learning
  + phase ordering + final decisions. W4 (Environment fingerprinting)
  scope expanded to include Wazuh log sources (alerts.json + manager
  logs) per operator direction Round 4; wazuh-indexer remains the
  canonical store. 2 remaining open decisions RESOLVED (alert-pattern
  cadence: configurable, default daily; environment fingerprinting:
  auto at org bootstrap). 5 new phases (7.5, 8.5, 9.5, 11.5, Phase 12
  rename) confirmed. wolf-hunt / wolf-den / wolf-pack names reserved
  for future ADRs 0021 / 0022 / 0023. Operator sign-off: ACCEPTED.

---

## Context

The operator captured a 17-point requirements list (2026-06-10, points 0-16)
describing where they want Wolf to go beyond the current build. The points
cluster into five architectural concerns:

| Cluster | Operator's points | What it's really asking for |
|---|---|---|
| **Central brain** | 0 | A single coherent cognitive layer, not 17 disconnected features |
| **Memory** | 1, 2, 3, 4 | Conversation memory + cross-conversation memory + environment knowledge |
| **Continuous learning** | 5, 6 | Wolf gets smarter from operator's environment over time |
| **Reasoning quality** | 7, 8, 9, 10 | Robust answers, self-validation, deep thinking |
| **Strategic action** | 11 | Tied to Phase 6 wolf-gateway (already scoped) |
| **Future platforms** | 12, 13, 14, 15 | wolf-hunt, wolf-den, wolf-pack — distinct sub-products |

This ADR proposes the architecture for the first four clusters as a single
integrated layer: the **Wolf Central Brain**. The fifth (future platforms) is
acknowledged + name-reserved + scoped at high level, with detailed designs
deferred to their own ADRs when those phases open.

This ADR does NOT decide:
- Model fine-tuning architecture (heavy, separate scoping needed)
- wolf-hunt's case-management schema (own ADR at Phase 9 open)
- wolf-den's threat-intel data model (own ADR at Phase 11 open)
- wolf-pack's relay protocol (own ADR at Phase 12 open)
- UI/UX for the memory layer (separate Phase 5.0c-style slice)
- Specific embedding model choice for the new memory store (likely re-uses
  the existing per-organization choice from ADR 0014)

---

## The Central Brain vision

> "I want wolf to have a central brain." — operator, 2026-06-10, point 0

Today's Wolf is **stateless per conversation**. Each chat turn is processed
against the current prompt + the retrieved RAG chunks + the agent loop's
tool outputs. Nothing persists between conversations (other than the audit
log, which isn't operator-visible). The model has no memory of who the
operator is, what their environment looks like, or what was discussed
yesterday.

The **Central Brain** is the proposed integrated cognitive layer that:

1. **Remembers** — across turns within a conversation (session memory) and
   across conversations within an organization + user (long-term memory).
2. **Knows the environment** — accumulates structured facts about the
   operator's Wazuh deployment, network topology, common alert shapes, prior
   incidents, tooling preferences.
3. **Thinks** — supports a new "deep-think" agent strategy alongside the
   existing frontier/guided/pipeline strategies; multi-step decomposition
   with verification between steps.
4. **Validates itself** — extends the existing grounding validator to also
   verify ACTIONS (not just answers) before they reach wolf-gateway. Plus
   pre-action sanity checks: "is this the right organization? Does this
   action align with the user's stated intent in this conversation?"
5. **Learns continuously** — extracts patterns from the operator's day-to-day
   Wazuh alerts + feedback + closed cases, feeds findings back into the
   per-organization knowledge corpus.

**Not the same as making Wolf "smarter"** — the underlying model doesn't
change. The Central Brain is the SCAFFOLDING around the model: what context
gets retrieved, what reasoning steps happen, how the model's output is
verified before it ships. Better scaffolding makes the same model produce
dramatically better outputs.

---

## Per-organization isolation — non-negotiable (load-bearing section)

**The Central Brain is centralized in CODE but decentralized in DATA.** Same
process, same agent loop, same set of strategies, same validators — but
every memory row, every learned fact, every observation is partitioned by
`organization_id` and forced-filtered at the SQL layer.

This is the SAME pattern Wolf already uses for RAG (ADR 0014) + audit
events + Wazuh credentials + cache (ADR 0010 / doc 05's four enforcement
points). This ADR extends the pattern to four new partitioned domains:

| New domain | Partition key | Same pattern as |
|---|---|---|
| `operator_memory` (long-term cross-conversation facts) | `(organization_id, user_id)` — BOTH required | `audit_events.organization_id + user_id` |
| `session_memory` (per-conversation running summary) | `organization_id` via `conversation_id → organization_id` | `conversations.organization_id` |
| `environment_entities` (semantic-memory knowledge graph) | `organization_id` | `knowledge_chunks.organization_id` |
| `environment_edges` (knowledge-graph relationships) | inherited via `environment_entities.organization_id` | (new, same forced-filter discipline) |

**Continuous-learning workers**: one worker invocation per organization,
never a single job that iterates across organizations. Same pattern as
`bootstrap_tenant.py` (to be renamed `bootstrap_organization.py`) — single-
org scope per invocation. The orchestrator iterates over orgs + dispatches
per-org workers.

**Cross-organization isolation test suite**: extends to cover all new
tables + workers. Currently tracks ~6 enforcement points; the ADR 0017
implementation grows it to ~10+. The CI gate stays load-bearing — no merge
to main without the suite passing.

### Storage vs UI: how a user's OWN memory crosses orgs

The partitioning above describes the **storage layer**: every memory row
has `organization_id` as a hard filter, and no query can read across org
boundaries except via explicit, audited UserOrganization membership.

The **UI layer** for a user's "My memory" page (per [ADR 0019](0019-web-first-configurability.md)
§"My memory: cross-org, self-only", ACCEPTED 2026-06-10) JOINs across all
of the current user's `UserOrganization` rows to produce a combined
self-view of THEIR OWN memory, each entry labeled by org name. This is
not a contradiction with the per-org partitioning:

- The partitioning protects org data from OUTSIDERS (Acme members
  cannot see Beta's memory; Superuser without explicit membership in
  Acme cannot see anyone else's Acme contributions to memory)
- A user reading their OWN memory across their OWN org memberships is
  not crossing an isolation boundary — they're aggregating self-data
  they're already entitled to see, from orgs they're already members of
- Superuser-self-only at the data-access level still applies: even
  with org-consent grants, no role can see another user's memory

See ADR 0019 for the UI semantics + Superuser-cannot-see-others
caveat. ADR 0017 (this ADR) is the source of truth for the storage
schema + partitioning discipline.

### MSSP scenario worked example

One Wolf install, three customer organizations:

```
                Wolf install
        ┌──────────────────────────┐
        │ Superuser "Wolf" (1 per  │
        │ install, fixed username) │
        └────────────┬─────────────┘
                     │
        Configures Wazuh component mapping
        (single-host OR distributed cluster)
                     │
                     ▼
       ┌─────────────┴──────────────┐
       │  Wazuh ecosystem            │
       │  (Indexer + Manager(s) +    │
       │   Dashboard)                │
       └─────────────┬───────────────┘
                     │
   ┌─────────────────┴─────────────────┐
   │   Per-org Wazuh API credentials   │
   │   (each org has its own; the      │
   │   Wazuh-side RBAC restricts each  │
   │   to its customer's data slice)   │
   └───────────────────────────────────┘
                     │
   ┌─────────────────┼─────────────────┐
   ▼                 ▼                 ▼
┌──────┐         ┌──────┐         ┌──────┐
│Acme  │         │Beta  │         │Gamma │
│Corp  │         │Inc   │         │Ltd   │
│      │         │      │         │      │
│Wolf  │         │Wolf  │         │Wolf  │
│Org A │         │Org B │         │Org C │
└──────┘         └──────┘         └──────┘
   │                 │                 │
   │  Memory:    Memory:        Memory:
   │  Acme's     Beta's          Gamma's
   │  data       data            data
   │  ONLY       ONLY            ONLY
   │
   └─ MSSP analyst Sarah:
      ├─ UserOrganization (Acme, role=Analyst)
      ├─ UserOrganization (Beta, role=Analyst)
      └─ UserOrganization (Gamma, role=Responder)

      At login: backend resolves all three memberships
      Dashboard: org-switcher lets Sarah switch context
      Each switch = new OrganizationContext + new
      memory/brain/knowledge partition. No cross-
      contamination ever.
```

**End-to-end isolation = Wazuh RBAC + Wolf per-org partition.** Both layers
must hold. Wolf never shares a credential across orgs; never queries memory
without an `organization_id` filter; never lets a worker iterate across
multiple orgs in one process.

### Wazuh access in MSSP

The Wolf Superuser configures + maps Wazuh components for the whole install
(single-host OR distributed cluster topology — see ADR 0020 for the
component-mapping design + UI). Per-org Wazuh CREDENTIALS are also
Superuser-only —
the most security-sensitive integration point in Wolf, concentrated in the
fewest possible identities.

For each organization, the Superuser configures the org's Wazuh API
credentials (restricted by the MSSP's Wazuh admin to that org's data
slice, typically via Wazuh groups + index DLS). Wolf uses each org's
credentials when querying Wazuh for that org. Wolf never holds a "master"
Wazuh credential that sees everything.

Why per-org Wazuh credentials (not a single shared one):
- **Defense-in-depth**: Wazuh's RBAC is the primary boundary; Wolf's
  filter is the secondary. Both must hold for isolation to fail.
- **Audit attribution**: Wazuh's audit log shows the correct
  `acme-api-user` accessed Acme's data, regardless of which org made the
  call from Wolf's side.
- **Industry convention**: every MSSP-shaped SOC tool does this — Wolf
  surprising operators by departing from the norm is bad UX.

---

## Architectural components

The Central Brain is **five subsystems** sharing infrastructure but each
solving a distinct problem:

```
┌──────────────────────────────────────────────────────────────────────┐
│  Operator query → wolf-server agent loop                              │
└──────────────┬───────────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  ┌─ 1. MEMORY LAYER ───────────────────────────────────────────────┐  │
│  │   • Episodic (in-conversation turns)                            │  │
│  │   • Session (current conversation's running summary)            │  │
│  │   • Long-term (cross-conversation, per operator)                │  │
│  │   • Semantic (environment facts — knowledge graph per org)      │  │
│  │  All forced-filtered by (organization_id, user_id) at SQL layer   │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                              │                                        │
│                              ▼                                        │
│  ┌─ 2. THINKING LAYER ─────────────────────────────────────────────┐  │
│  │   Three existing strategies (frontier / guided / pipeline)       │  │
│  │   + NEW deep-think strategy:                                     │  │
│  │     1. Decompose question into sub-questions                     │  │
│  │     2. Per sub-question: retrieve + answer + grounding-check     │  │
│  │     3. Synthesize sub-answers into the final response            │  │
│  │     4. Final grounding pass against accumulated evidence         │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                              │                                        │
│                              ▼                                        │
│  ┌─ 3. SELF-VALIDATION LAYER ──────────────────────────────────────┐  │
│  │   • Grounding validator (existing, Phase 3) — verifies facts     │  │
│  │   • NEW: Action validator — pre-action checks (org correct?     │  │
│  │     intent aligned? blast radius bounded?)                       │  │
│  │   • NEW: Confidence calibration — Wolf signals certainty         │  │
│  │     correctly (NOT "never says I don't know" — see §X)           │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                              │                                        │
│                              ▼                                        │
│  ┌─ 4. CONTINUOUS LEARNING LAYER ──────────────────────────────────┐  │
│  │   Background workers (separate from chat path):                  │  │
│  │   • Knowledge feedback loop (Phase 10, already planned)          │  │
│  │   • NEW: Alert-pattern extraction (analyzes Wazuh stream,        │  │
│  │     surfaces emergent patterns, adds findings to corpus)         │  │
│  │   • NEW: User feedback embeddings (thumbs-up/down → tuning       │  │
│  │     signal for retrieval ranking)                                │  │
│  │   • NEW: Environment fingerprinting (network topology, tooling,  │  │
│  │     common alert shapes → semantic memory)                       │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                                                                       │
│  ┌─ 5. KNOWLEDGE STORE (existing, ADR 0014) ───────────────────────┐  │
│  │   PgvectorKnowledgeStore — hybrid retrieval, multi-embedding +   │  │
│  │   FTS + RRF. Memory layer + learning layer both write here.      │  │
│  └─────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

### Subsystem 1: Memory layer

Four kinds of memory, each with distinct storage + lifecycle:

#### Episodic memory (in-conversation turns)
- **What**: the raw turns of the current conversation
- **Storage**: existing `conversations` + `messages` tables
- **Lifecycle**: persists indefinitely; operator can delete a conversation
- **Status**: ✅ already built (chat-history feature)

#### Session memory (current conversation's running summary)
- **What**: a continuously-updated structured summary of the current
  conversation. Auto-compresses long sessions so the model gets context
  without re-reading every turn.
- **Storage**: NEW table `session_memory(conversation_id, summary, embedding,
  updated_at)`. One row per active conversation.
- **Lifecycle**: regenerated periodically (every N turns OR every N tokens
  in the conversation). Persists with the conversation.
- **Status**: NEW — needs implementation.

#### Long-term memory (cross-conversation, per operator)
- **What**: notable facts the model decides to remember across conversations.
  "Operator's primary monitored network is 10.0.0.0/8." "Operator prefers
  active-response actions over pure alerting." "Operator's runbook for
  PowerShell-Empire alerts is X."
- **Storage**: NEW table `operator_memory(id, organization_id, user_id,
  fact_type, fact_text, embedding, confidence, source_conversation_id,
  created_at, expires_at, deleted_at)`.
  - `fact_type`: enum (6 categories, finalized Round 2 review 2026-06-11):
    - `preference` — how the operator likes things presented / what
      they care about
    - `environment_fact` — about their Wazuh deployment, network,
      hosts, services
    - `runbook` — specific procedures the operator follows
    - `social_context` — operator's role, peers, org context
      (renamed from `relationship` in Round 2 for specificity)
    - `observation` — general observations / catch-all
    - `incident_lesson` — lessons learned from past investigations
      ("tried X for this kind of alert; worked / didn't work")
  - `confidence`: float [0-1] — how sure the model is this fact is right
    - Decay function: **exponential half-life** (default half-life
      30 days; configurable per-fact-type later if needed)
    - Auto-prune threshold: confidence < 0.1 → fact is soft-deleted
      (set `deleted_at`, retained for audit but excluded from retrieval)
  - `expires_at`: optional — facts can age out by TTL independent of
    confidence decay (e.g., "operator on call this week" expires
    after 7 days)
- **Lifecycle**: written by the model when it detects a recall-worthy fact.
  Pruned via:
    1. Explicit operator deletion (right-to-be-forgotten via dashboard)
    2. Auto-expiry via `expires_at`
    3. Confidence decay below 0.1 threshold (per-fact half-life)
    4. Per-fact-type TTL when `expires_at` not set (defaults per type
      — see "Retention policy" in Round-2 resolved decisions below)
- **Retrieval**: **load-once at conversation start** (v1 design,
  confirmed Round 2). The agent loop queries `operator_memory` for
  facts relevant to the operator's first message (vector similarity).
  Top-K facts injected as context. Mid-conversation re-query when
  topic shifts is OUT OF SCOPE for v1; the session memory layer
  evolves to capture topic-shift context within a conversation.
- **Status**: NEW — needs implementation. Core of the "remembers" capability.

#### Semantic memory (environment knowledge graph)
- **What**: structured facts about the operator's Wazuh deployment.
  "Agent 042 monitors host db-prod-01." "Rule 5710 maps to MITRE T1078."
  "Cluster has 3 indexer nodes." Forms a graph of entities + relationships.
- **Storage**: NEW tables in Wolf's existing Postgres
  (`wolf-database`) — `environment_entities(id, organization_id, type,
  name, attributes_jsonb)` + `environment_edges(from_id, to_id, relation,
  weight)`. Entity types: host / agent / user / rule /
  mitre_technique / network / service / cve.
- **Storage choice rationale (Round 2 review, 2026-06-11)**: Postgres
  tables chosen over a dedicated graph DB (Neo4j Community / Memgraph)
  for v1. Reasons:
  - Wolf's expected scale (~1k-10k entities + ~10k-100k edges per org)
    fits Postgres comfortably; queries are 1-3 hops, handled by JOINs
    or recursive CTEs in milliseconds
  - Zero new infrastructure (uses existing `wolf-database`); operator
    deployment stays simple
  - Airgap-friendly; no new service to deploy + backup + upgrade
  - Migration path preserved: if Wolf v3+ ever exceeds Postgres scale,
    the schema is trivially exportable (`(id, type, name, attributes)`
    + `(from, to, relation, weight)`) to Neo4j or equivalent
  - Memgraph evaluated + rejected due to BSL non-production
    restriction in Community Edition
  - Real graph DB shines for 10+ hop traversals, billions of edges,
    real-time graph algorithms, Cypher pattern matching — none of
    which apply to Wolf's v1 workload
- **Lifecycle**: written by the continuous-learning layer (subsystem 4) +
  manually via the operator's dashboard. Pruned by operator action only.
- **Retrieval**: graph-walk queries during agent reasoning ("what hosts does
  agent X monitor? what rules fire on those hosts?")
- **Status**: NEW — biggest single piece of work in this ADR.

### Subsystem 2: Thinking layer

Today's agent loop has three strategies (ADR 0001):
- **Frontier** — full autonomy, model plans + acts (used with top-tier models)
- **Guided** — orchestrator scaffolds the loop, model fills in steps
- **Pipeline** — deterministic outer loop, model is constrained per step

This ADR adds a fourth:

#### Deep-think strategy
For complex questions where the model would benefit from explicit
decomposition before answering:

1. **Decompose** — break the question into sub-questions (e.g., "What
   happened on host X yesterday?" → "List alerts on X yesterday" + "Group by
   rule" + "Identify timeline anomalies" + "Cross-reference with similar
   hosts").
2. **Per sub-question loop**:
   - Retrieve relevant chunks (RAG + memory)
   - Generate sub-answer
   - Grounding-check the sub-answer
   - If a sub-answer's verdict is Not Verified, decompose FURTHER or flag
3. **Synthesize** — combine sub-answers into the final response
4. **Final grounding pass** — verify the synthesis against all sub-answer
   evidence
5. **Confidence summary** — explicit per-sub-answer confidence chips in the
   UI

**When to use**: when the operator's question requires multi-hop reasoning
OR when the first-pass answer has Uncertain/Not Verified verdicts (escalate
to deep-think for retry).

**Cost**: slower (multiple model calls), more tokens. Use sparingly. The
operator can manually invoke via a "Deep Think" button.

### Subsystem 3: Self-validation layer

Today: grounding validator (Phase 3) verifies the FACTS in an answer.

New additions:

#### Action validator (for propose tools)
Before any `propose_*` tool's output reaches wolf-gateway, an LLM-as-judge
pass verifies:
- Target identity matches the operator's stated intent ("operator asked for
  host X, propose is targeting host X")
- Blast radius is bounded ("operator asked to isolate ONE agent, propose
  isn't isolating the whole org")
- Organization context is correct (defense-in-depth alongside the existing
  forced-filter per ADR 0010)
- The action aligns with the conversation's stated outcome

If the validator says "misaligned", the proposal is REJECTED before it ever
reaches the approval queue.

**Round 3 design choices (2026-06-11):**

- **Hard gate, no bypass.** If the validator rejects a proposal, the
  operator must rephrase + retry. There is no Admin / Superuser /
  emergency override path that lets a rejected proposal reach
  approval. The validator's job IS to catch bad proposals; bypass
  paths defeat the purpose. If false-positive rejection becomes a
  measured real-world problem, an Admin-override path can be added
  in v1.1 — but v1 ships strict.
- **No cost cap on the validator.** Unlike deep-think (a perf cost
  on user requests, where a cap is appropriate), the action
  validator is a SAFETY cost — skipping it means a bad propose-
  action reaches approval. Skipping is never worth the savings.
  Every propose-action goes through validation, full stop.
- **Rejection UX: inline + "Edit and retry".** When the validator
  rejects, the operator sees the rejection reason in-line in the
  current conversation (NOT a separate page; NOT a modal that loses
  context). An "Edit and retry" affordance lets the operator
  rephrase the action + resubmit without starting over. The
  rejection event is audit-logged (per ADR 0018 audit-event schema).

#### Confidence calibration
Wolf signals certainty correctly. **Three states** for any answer
(operator-confirmed Round 3; 4-state granularity rejected as more
than operators can meaningfully act on):
- **Confident + verified** — RAG evidence + grounding validator both clean
- **Confident with caveat** — partial evidence; Wolf states the caveat
- **Insufficient evidence** — explicitly says so + offers next-step actions

This is the proper handling of the operator's point 8 — see §"Robust answer
posture" below.

### Subsystem 4: Continuous learning layer

Background workers that run independently of the chat path. All write to the
shared knowledge store (subsystem 5).

#### Worker 1: Knowledge feedback loop (already planned in Phase 10)
Operator-reviewed case-close summaries auto-ingest into the tenant's
private corpus. Already designed in `docs/06-knowledge-and-rag.md` +
`docs/08-reporting-and-orchestration.md`.

#### Worker 2: Alert-pattern extraction
Periodic job (e.g., hourly) that:
- Reads recent Wazuh alerts for the tenant
- Clusters by rule + agent + time window
- For each cluster, asks: "Have we seen this pattern before? Is it
  worth remembering?"
- Promotes recurring patterns to semantic memory as `environment_entities`
  of type `observation`

#### Worker 3: User feedback signal
- Operators thumbs-up/down on Wolf's answers
- Negative feedback → embedding of the question gets tagged "previously
  unhelpful retrieval"
- Future retrievals for similar questions weight DOWN the same chunks
- Positive feedback → the reverse (boost those chunks for similar queries)

#### Worker 4: Environment fingerprinting
- One-shot at organization bootstrap + periodic refresh
- Walks the Wazuh API + indexer to enumerate (per ADR 0020 per-org
  credentials):
  - **Agents** (Wazuh API `/agents`)
  - **Hosts** (derived from agent metadata; cross-referenced with
    network topology)
  - **Rules** (Wazuh API `/rules` + custom rules in `/var/ossec/etc/rules/`)
  - **Groups** (Wazuh API `/agents/groups`)
  - **Network topology** (where visible — derived from agent IPs +
    operator-declared subnets)
  - **Wazuh log sources** (Round 4 operator addition, 2026-06-11):
    - `alerts.json` — the realtime alerts log written by the
      Wazuh manager (default `/var/ossec/logs/alerts/alerts.json`)
    - `archives.json` — archived events from the manager
    - Manager logs (`/var/ossec/logs/ossec.log`, agent buffer logs)
    - Indexer-side indices (`wazuh-alerts-*`, `wazuh-monitoring-*`,
      `wazuh-statistics-*`)
    - **NOT replicated into Wolf's DB** — the wazuh-indexer remains
      the canonical store for log content. Wolf tracks log SOURCES
      as semantic-memory entities (so Wolf knows "alerts.json lives
      at <path> on the manager + is mirrored to wazuh-alerts-*
      indices in the indexer") + queries the indexer for real-time
      content via the per-org Wazuh API credentials. Avoids storage
      explosion + duplication of the indexer's role.
- Populates `environment_entities` + `environment_edges` — log sources
  become entities of type `log_source` with edges to the agents/hosts
  they originate from + the indexer indices they land in
- Operator can review + edit via the dashboard

---

## Robust answer posture (push back on point 8)

> "I really don't want, that is, wolf saying it can't or it doesn't know or
> it doesn't have an answer." — operator, point 8

**This ADR explicitly does NOT honor that requirement as stated** — and the
push back is important enough to capture in writing here so it doesn't get
re-litigated later.

### Why "never say I don't know" is dangerous

| Scenario | If Wolf must always answer | If Wolf can say "uncertain" |
|---|---|---|
| Operator asks about a CVE Wolf has no evidence for | Wolf fabricates plausible-sounding details | Wolf says "I don't have data on CVE-X in your environment; here's what's commonly known + here's what to check" |
| Operator asks during an active incident | Wolf hallucinates an attribution | Wolf says "These three indicators are consistent with X family but I don't have enough evidence to assert it; here's what would confirm" |
| Operator asks about a host Wolf doesn't see | Wolf invents the host's state | Wolf says "I don't see host Y in your monitoring; is it newly added?" |

In each case, the "always answer" version is **worse for the operator** —
they make decisions based on confabulation. Real incident response demands
**calibrated uncertainty**.

### What this ADR commits to instead

Three pillars (none of which require Wolf to ever bluff):

#### Pillar 1: Try harder before yielding
The deep-think strategy + memory retrieval + alert-pattern context mean
Wolf has MORE information to draw on than today's single-pass agent loop.
"I don't know" should be rarer because Wolf actually knows more.

#### Pillar 2: Never abdicate without a next step
The forbidden answer is "I don't know" with nothing else. The required
answer when knowledge is insufficient:

> "I don't have direct evidence in your environment for [specific
> question]. Based on [general knowledge / similar past cases / Wazuh
> documentation], here's what's typically true: [...]. To verify in YOUR
> environment, here's what to check: [actionable steps]. I can run [tool X]
> for you if you want."

This is honest + maximally useful. Always provides a path forward.

#### Pillar 3: Transparency over confidence theater
The 4-verdict taxonomy already does this — Verified vs Uncertain vs Not
Verified gives operators a clear signal. Future work: extend to the answer
level (per-claim chips already exist; add per-answer overall verdict).

### Concrete operator-experience contract

| Operator wants | Wolf delivers |
|---|---|
| Always a useful answer | ✅ Yes — even when Wolf doesn't know, it offers next steps |
| Never an unexplained "I don't know" | ✅ Yes — every uncertainty includes context + actionable next steps |
| Always confident | ❌ No — Wolf signals when it's guessing |
| Never says "uncertain" | ❌ No — Wolf will say "uncertain" when honest about evidence |

If "never says I don't know" means the first two, this ADR delivers it
completely. If it means the latter two, this ADR explicitly disagrees.

---

## Phase ordering recommendation

The Central Brain work doesn't fit cleanly into a single phase. Proposing
this re-sequence of the post-5.10 roadmap:

| Phase | Original | Proposed | Notes |
|---|---|---|---|
| 6 | Approval Gateway | Approval Gateway | Unchanged |
| 7 | Cases & reporting | Cases & reporting → **wolf-hunt foundation** | Extended scope: incident-level case management with alert correlation |
| **7.5 (NEW)** | — | **Central Brain — memory + deep-think + self-validation** | This ADR's core implementation work |
| 8 | Detection engineering | Detection engineering | Unchanged |
| **8.5 (NEW)** | — | **Central Brain — continuous learning workers** | Alert-pattern extraction, env fingerprinting, feedback signal |
| 9 | Playbooks & orchestration | Playbooks & orchestration | Unchanged |
| **9.5 (NEW)** | — | **wolf-hunt — Incident Response + Case Management platform** | Builds on Phase 7's foundation; own ADR at open-time |
| 10 | Knowledge feedback growth | Knowledge feedback growth | Already overlaps with Phase 8.5; might merge |
| 11 | Integrations | Integrations | Unchanged |
| **11.5 (NEW)** | — | **wolf-den — Cyber Threat Intelligence platform** | Own ADR at open-time |
| 12 | Wolf Knowledge Relay | **wolf-pack — agents on Wazuh hosts (renamed)** | "wolf-pack" replaces "Wolf Knowledge Relay" name; scope expanded to bidirectional (agents can ALSO execute actions Wolf can't from outside) |
| 13 | Auto-execution | Auto-execution | Unchanged |

**Net new phases**: 7.5, 8.5, 9.5, 11.5, plus a rename of Phase 12.

**No phases removed.** Existing ADRs stay valid.

---

## Reserved names for future ADRs

Per the operator's naming preferences (point 16):

- **wolf-hunt** — Incident Response + Case Management. Future ADR
  (number TBD, expected ~0021) when Phase 7/7.5 opens. Will cover:
  alert→case correlation algorithm, case data model, timeline
  construction, eradication-step generation, dashboard UI.
- **wolf-den** — Cyber Threat Intelligence Platform. Future ADR
  (number TBD, expected ~0022) when Phase 11.5 opens. Will cover:
  IOC extraction from environment, threat-actor profiling, report
  generation, intel-share format.
- **wolf-pack** — Native agents on Wazuh hosts. Future ADR (number
  TBD, expected ~0023) when Phase 12 opens. Will cover: relay daemon
  architecture, mTLS authentication per agent, bidirectional command
  channel, health checks, autonomous execution scope.

(Numbering note: the original ADR 0017 draft suggested 0018/0019/0020
for these three names. Those numbers have since been used by Bootstrap
Superuser/RBAC (0018), Web-first configurability (0019), and Superuser-
owned Wazuh mapping (0020) respectively. Updated to point at the next
available block 0021/0022/0023.)

These names are RESERVED in this ADR — they won't be used for anything else.
The existing `wolf-knowledge-relay.md` memory entry will be renamed to
`wolf-pack` in a follow-up commit.

---

## Open architectural decisions

Things this ADR DOES NOT decide. Each needs operator input before
implementation work starts on the affected subsystem:

### Memory layer (all 4 RESOLVED — Round 2 review, 2026-06-11)

1. **Retention policy — RESOLVED: 12 months default + per-fact-type
   overrides.** Per-type defaults:
   - `preference`: forever (until operator deletes) — high-value
     stable facts
   - `environment_fact`: 12 months — environment changes; stale facts
     should age out
   - `runbook`: forever (until operator deletes) — high-value stable
     procedures
   - `social_context`: 12 months — peer/role context can change
   - `observation`: 90 days — short-shelf-life catch-all
   - `incident_lesson`: forever (until operator deletes) — high-value,
     rare facts; never auto-expire
   - Override per fact via `expires_at` field; operator can also
     delete explicitly via the dashboard. Aligns with GDPR data-
     minimization principle while preserving high-value memory.

2. **Opt-in vs always-on — RESOLVED: Always-on with operator opt-out.**
   Memory records by default; user disables via settings (UI: User
   → Settings → Privacy → "Wolf is learning about your environment"
   toggle). Memory is what makes Wolf useful over time; opt-in
   default means most users never get the benefit. Opt-out path
   preserves privacy-conscious flexibility + clear disclosure
   ("Wolf is learning — see settings to manage") at first login.

3. **Cross-tenant (now: cross-organization) boundaries — RESOLVED:
   Confirmed.** No cross-organization memory under any circumstance.
   Memory rows hard-partitioned per `(organization_id, user_id)`.
   Already locked by ADR 0018 design (org-consent gate) + ADR 0017
   storage partitioning. ADR 0019 cross-org "My memory" UI is a
   self-view aggregation (the user's own data across their own
   memberships) — not a cross-org leak.

4. **Inspection UI scope — RESOLVED: Read + delete.** "My memory"
   page (per ADR 0019) shows all entries + each entry has a
   per-row delete button. NO edit (would let operator gaslight Wolf
   into believing false facts about their environment, corrupting
   retrieval — simpler to delete the bad fact + let Wolf re-learn
   from new conversations). Right-to-be-forgotten + transparency
   delivered without the edit-trap.

### Other Round-2 design choices (RESOLVED, embedded in Memory layer description above)

- **`fact_type` enum — 6 categories** (preference / environment_fact /
  runbook / social_context / observation / incident_lesson). Renamed
  `relationship` → `social_context` for specificity; added
  `incident_lesson` for high-value investigation-derived facts.
- **Confidence decay — Exponential half-life** (default 30 days);
  auto-prune at confidence < 0.1 (soft delete, retained for audit).
- **Retrieval timing — Load-once at conversation start.** Vector
  similarity against the operator's first message; top-K facts
  injected as context. Mid-conversation re-query when topic shifts
  is OUT OF SCOPE for v1 (session memory captures within-conversation
  evolution).
- **Semantic memory storage — Postgres tables in `wolf-database`**
  (no dedicated graph DB for v1). Migration path to Neo4j preserved
  if Wolf v3+ exceeds scale.

### Thinking layer (both RESOLVED — Round 3 review, 2026-06-11)

5. **Deep-think trigger — RESOLVED: Both (operator-explicit + auto-
   escalate).** Operator can click a "Deep Think" button on the chat
   input for the "I know this is complex" case; AND auto-escalation
   triggers when the first-pass grounding validator returns Uncertain
   or Not Verified (the "first-pass wasn't good enough" case). Two
   trigger paths, same execution. Costs more on auto-escalate hits
   but gives a better answer.

6. **Per-conversation cost cap — RESOLVED: Soft cap with warning.**
   Wolf tracks per-conversation deep-think invocations + tokens;
   shows a visible "you've used N deep-think cycles in this
   conversation" pill once the threshold is crossed. Hard caps
   frustrate analysts in long investigations; no cap leads to
   runaway costs. Default threshold (e.g., 5 deep-think cycles per
   conversation) is operator-configurable per install via the
   future ADR-0019 settings surface.

### Continuous learning (both RESOLVED — Round 4 review, 2026-06-11)

7. **Alert-pattern extraction cadence — RESOLVED: Operator-configurable
   per org, default daily.** Daily is the right default for most orgs
   (alert volume + pattern recurrence operate on day-scale cycles);
   high-volume orgs may want hourly + low-volume orgs may want weekly.
   Per-org configuration via the future ADR-0019 settings surface
   (Engineer/Admin can adjust). Default daily so it works out-of-the-box.

8. **Environment fingerprinting consent — RESOLVED: Automatic at org
   bootstrap + periodic refresh.** Environment fingerprinting is what
   makes Wolf useful for the org — opt-in default means many orgs run
   with degraded Wolf. Auto-on delivers the value by default; org
   Admins can disable via settings if they have a specific
   compliance/data-classification concern. Also, **scope expansion
   (Round 4 operator direction)**: W4 also enumerates Wazuh log sources
   (alerts.json, archives.json, manager logs, indexer indices) as
   semantic-memory entities of type `log_source`. Wolf does NOT
   replicate log content into its DB — the wazuh-indexer remains
   canonical; Wolf is the query + reasoning layer on top. See
   §"Worker 4: Environment fingerprinting" above for details.

### General (RESOLVED — Round 3 review, 2026-06-11)

9. **"Robust answer posture" disagreement — RESOLVED: §"Robust
   answer posture" accepted as written.** Operator accepted the
   ADR's posture in full after Round 3 walkthrough:
   - Wolf delivers rows 1-2 of the operator-experience contract:
     "always a useful answer" + "never an unexplained 'I don't
     know'" (every uncertainty includes context + actionable next
     steps + tool offers per Pillar 2's required template)
   - Wolf explicitly rejects rows 3-4: "always confident" + "never
     says uncertain" — these would push Wolf to hallucinate during
     incident response, which is a SOC safety regression, not a UX
     improvement
   - The three pillars (try harder before yielding; never abdicate
     without a next step; transparency over confidence theater)
     stand as the design contract
   - The 4-verdict taxonomy (Verified / Uncertain / Not Verified /
     Non-factual) continues to be Wolf's calibrated-uncertainty
     mechanism + the per-answer overall verdict extension lands
     with Phase 7.5 work
   - This was the gating decision for the whole ADR; with it
     resolved, ADR 0017 can move to ACCEPTED after Round 4 closes
     the remaining 2 open decisions (continuous learning).

---

## Implementation sequencing (after this ADR is ACCEPTED)

Recommended order if all clusters get green-lit:

1. **Phase 7.5 first** (memory + deep-think + self-validation):
   - 7.5-a: Memory schema + tables migration
   - 7.5-b: Long-term memory write path (model decides what to remember)
   - 7.5-c: Long-term memory retrieval at conversation start
   - 7.5-d: Session memory + auto-compression
   - 7.5-e: Semantic memory (environment knowledge graph) schema + entities
   - 7.5-f: Deep-think agent strategy
   - 7.5-g: Action validator
   - 7.5-h: Operator-facing memory dashboard
   - 7.5-i: Close-out (smoke + docs)

2. **Phase 8.5 next** (continuous learning):
   - 8.5-a: Alert-pattern extraction worker
   - 8.5-b: User feedback signal
   - 8.5-c: Environment fingerprinting worker
   - 8.5-d: Close-out

3. **Phase 9.5 — wolf-hunt** (its own ADR at open time)

4. **Phase 11.5 — wolf-den** (its own ADR at open time)

5. **Phase 12 — wolf-pack** (its own ADR at open time)

---

## Status, sign-off, next steps

This ADR is now **ACCEPTED**. Operator sign-off after a 4-round review
(2026-06-10 to 2026-06-11) closed every previously-open architectural
decision — see the revision history at the top of this file for the
round-by-round summary.

| Round | Topic | Outcome |
|---|---|---|
| 1 | Orientation + 5+1 clustering + cross-ADR consistency | ✓ Closed (clustering confirmed; ADR-0019 "My memory" cross-ref added to per-org isolation section) |
| 2 | Memory architecture (Subsystem 1) | ✓ Closed (4 decisions + 4 design choices: 6-category fact_type, exponential decay, load-once retrieval, Postgres-tables for semantic memory) |
| 3 | Thinking + Self-validation + point-8 disagreement | ✓ Closed (deep-think trigger both-paths + soft cost cap; validator hard-gate + 3 confidence states + no cap + inline rejection; §"Robust answer posture" ACCEPTED as-written) |
| 4 | Continuous learning + phase ordering + sign-off | ✓ Closed (alert-pattern cadence configurable default-daily; environment fingerprinting auto + scope expanded to Wazuh log sources with indexer-as-canonical; 5 new phases + wolf-hunt/den/pack name reservations confirmed; ACCEPTED) |

With ACCEPTED status, the roadmap entries for Phases 7.5 + 8.5 + 9.5 +
11.5 + Phase 12 (wolf-pack rename) become real future work units;
wolf-hunt / wolf-den / wolf-pack remain reserved-named pending their
own dedicated ADRs (expected ~0021/0022/0023 at open-time).

This ADR is a design contract, not an implementation commitment. No
code ships from this ADR's close; the 4 subsystems each become their
own implementation slices when the corresponding phases open.

Implementation will be sequenced AFTER Phase 6.4 (tenant→organization
rename, per ADR 0018) + Phase 6.5 (Bootstrap + RBAC + Login UX, per
ADR 0018) + Phase 6.6 (Wazuh component mapping, per ADR 0020), since
the Central Brain's memory schema uses the `organization_id` + `user_id`
+ `UserOrganization` membership model defined in those ADRs.
