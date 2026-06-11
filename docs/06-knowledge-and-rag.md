# 06 — Knowledge and RAG Layer

This is where "the platform has complete knowledge of the whole Wazuh deployment"
becomes real — without becoming a giant static brain that confidently tells
analysts things that stopped being true an hour ago.

## The split — live state vs stable knowledge

"Knowledge" is not one thing. It is two, with opposite properties.

| | Live state | Stable knowledge |
|---|---|---|
| Examples | Which agents exist now, current cluster health, the rules in effect today, which agents are disconnected this minute | Wazuh docs, ATT&CK descriptions, runbooks, past incident write-ups |
| Change rate | Minutes | Weeks or months |
| Truth requirement | **Must be current**; a stale answer is worse than no answer | Slowly evolving; reasonably current is fine |
| Mechanism | **Fetched fresh through read tools** | **Retrieved through RAG** |

The rule is blunt: **live state is fetched through tools, never retrieved from a
knowledge store. Stable knowledge is retrieved through RAG, never hardcoded.** Do
not cache live state into the vector store "for convenience" — you will produce a
confidently-wrong platform.

## The three corpora — and which are organization-scoped

The stable-knowledge store is three distinct corpora with different ownership and
update rhythms.

### 1. Wazuh product knowledge — shared across all organizations

The official documentation, rule and decoder syntax, indexer/server/agent
architecture, configuration semantics. Identical for every customer, so a single
shared partition. Powers answers like "what does this rule option do" or "how does
active response get configured."

Refreshed when Wazuh releases.

### 2. Threat-intelligence knowledge — shared, versioned

MITRE ATT&CK techniques, tactics, mappings; CVE context. Shared (public data), but
ATT&CK gets revised — so this corpus needs a refresh pipeline and a **version
stamp**, because an incident mapped to ATT&CK should be traceable to *which*
ATT&CK version.

### 3. Organization-private knowledge — strictly per-organization; the most sensitive corpus

This customer's runbooks, standard operating procedures, past incident write-ups,
internal notes on known false positives. This is what makes the agent feel like it
knows *this* SOC and not a generic one. It is also the corpus a cross-organization
retrieval bug would expose catastrophically.

Lives in a per-organization partition. The organization-scoping rules from `05` apply without
exception: a retrieval call can only ever search the requesting organization's private
partition.

## Ingestion — where RAG quality is decided

RAG lives or dies on chunking and metadata. Security content punishes naive
ingestion.

### Chunk on structure, not character count

A Wazuh rule, a decoder, a runbook procedure — these are self-contained units.
Splitting a rule across two chunks so neither is retrievable in full is a common
failure. Chunk along the document's natural boundaries: one rule per chunk, one
procedure per chunk, one technique per chunk where possible.

### Metadata is half the system

Every chunk carries structured metadata:

- `source_type` — `wazuh_doc` / `attack` / `runbook` / `past_incident`.
- `organization_id` — for the private corpora.
- `wazuh_version` — when applicable.
- `attack_version` — for ATT&CK chunks.
- For runbooks and incident reports: which **rule IDs**, **ATT&CK techniques**, and
  **alert types** the chunk pertains to.

This metadata lets retrieval **filter before it ranks**. When the agent is
investigating a specific rule ID, you want the runbook chunks tagged with that rule
ID, not whatever is semantically nearest.

### Hybrid retrieval (vector + keyword)

Security queries are full of exact tokens — rule IDs (`5710`), CVE numbers, ATT&CK
technique IDs (`T1110`), exact process names. Pure semantic search is bad at
exact-match; it ranks "something about brute force" over the chunk that literally
references rule 5710.

Use **hybrid retrieval**: combine BM25/keyword scoring with vector scoring. This
single choice noticeably improves answer quality, and it also makes weaker
embedding models viable — keyword recall covers their semantic gaps.

### Freshness pipelines

- **Shared corpora** are re-ingested on Wazuh releases and ATT&CK updates.
- **Private corpora** grow automatically — every closed incident and new runbook
  flows back in (with sanitization, see below), so the agent gets smarter about
  that organization's environment over time.

That feedback loop is a genuine moat: the longer a customer uses the platform, the
better it knows their SOC.

## The two failure modes specific to a security knowledge layer

### Hallucinated grounding

The agent retrieves a runbook chunk and, in summarizing, invents a step that isn't
there — or cites a rule that doesn't exist. In a security context this is not a
cosmetic error: analysts may act on a fabricated remediation step.

**Mitigations:**
- Retrieved chunks are passed to the agent as **quotable, attributable evidence**.
- Answers grounded in knowledge **must cite which chunk and source** they came
  from, so a human can verify.
- For procedural or response-related content, the agent should **prefer quoting the
  runbook over paraphrasing** it.
- **Live-state claims must come from a tool call**, never from "knowledge." If the
  agent says "web-07 is offline," that statement traces to `get_agent_status`, not
  a retrieved document.
- A **grounding validator** runs over the agent's draft answer and confirms that
  factual claims map to either a tool result or a retrieved chunk. Ungrounded
  claims are flagged and either removed or surfaced as "unverified" to the user.

### Poisoned knowledge via the private corpus

If past incident write-ups auto-ingest, and an incident report contains attacker-
controlled text (a malicious filename, a crafted log excerpt quoted in the report),
attacker content is now indexed into the knowledge base — and a future retrieval
could surface a planted instruction. This is prompt injection with a time delay.

**Mitigations:**
- Treat ingested content as **untrusted data**, the same as live log data.
- Retrieved chunks are **evidence to reason over**, never **instructions to
  follow.** The agent's separation of "data I read" from "actions I can take" (the
  whole capability-tier design) is what contains this.
- **Sanitize/review** what auto-ingests. Auto-ingestion of raw log excerpts is
  particularly risky; prefer auto-ingestion of *structured* incident summaries
  produced by the platform itself, where attacker-controlled text is contained in
  designated quoted-evidence fields the agent is trained to treat as data.

## How "complete knowledge" actually gets delivered

The agent answers a deployment question by **combining both paths**.

Example: "Why is web-07 generating brute-force alerts and what should I do?"

- **Live tools** for the current alerts and the agent's current state:
  `search_alerts`, `get_event_timeline`, `get_agent_status`.
- **RAG over Wazuh docs** to explain what rule 5710 does:
  `query_runbook` with metadata filter `source_type=wazuh_doc`,
  `rule_id=5710`.
- **RAG over ATT&CK** for the T1110 technique context:
  `query_runbook` with `source_type=attack`, `technique=T1110`.
- **RAG over the organization's runbooks** for established response procedure:
  `query_runbook` with `organization_id=...`, `source_type=runbook`,
  `rule_id=5710`.

The agent composes one answer that cites all of it. That is "complete knowledge":
**a discovery layer for what is true now plus a retrieval layer for what is known,
fused per question.**

## Implementation notes

- The vector store should be self-hostable and free. Recommended: **OpenSearch's
  own vector capabilities** (you already run OpenSearch via Wazuh, but use a
  *separate* cluster for Wolf's RAG to keep concerns separated and isolation
  clean), or **Qdrant**, **Weaviate**, or **PostgreSQL with pgvector**.
- The embedding model should also be local-friendly. Sentence-transformer models
  (BGE, E5, GTE) run on CPU acceptably and produce strong embeddings for English
  technical text. Make the embedding model configurable; default to a strong open
  model.
- **Re-embedding is expensive.** Tie chunk records to embedding-model identity, so
  changing the embedding model triggers a planned re-embedding rather than silent
  inconsistency.
- The `query_runbook` tool should accept metadata filters as first-class arguments,
  not as free-text query content. This keeps retrieval deterministic and lets the
  agent narrow precisely.
