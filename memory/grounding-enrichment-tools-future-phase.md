---
name: grounding-enrichment-tools-future-phase
description: Future phase idea — build dedicated tools that supply more ground-truth evidence so grounding can mark more claims as Verified
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

User clarified (2026-05-28) that the idea is *not* a separate "grounding tools" category — it's adding more **supporting tools** that happen to enrich Wolf's evidence dictionary. Every new tool, regardless of its main purpose, automatically expands what the grounding judge can verify against. The "dedicated phase" framing is just one of two ways to act on this; continuous evaluation of every new tool's evidence value is the other.

**Why:** Wolf's grounding strength is bounded by the evidence Wolf can fetch. Every general-knowledge or inferential claim with no tool/RAG source today gets a yellow Uncertain. More tools with citeable output → more Verified verdicts → stronger user trust.

**How to apply:**
- **Continuously, in any phase:** when scoping a new tool, evaluate it on "what claims does this make verifiable?" alongside the tool's main purpose. Prefer tools whose output is structured + cite-able.
- **As a dedicated phase (post-RBAC):** focused work prioritising tools by evidence value. Candidate list: `get_agent_details`, `lookup_ip_reputation`, `get_attack_technique` (MITRE), `get_cve_details`, `quote_runbook` (exact-passage retrieval with line numbers), expanded `get_rule_definition` coverage.
- Each tool tenant-scoped via the existing TenantScopedQueryBuilder/PgvectorKnowledgeStore patterns. External feeds (IP rep, CVE) need an API-key plumbing pattern that respects the secrets backend.
- Update ADR 0015 and the validator prompt once the new evidence-source vocabulary stabilises — the judge needs to know these new source types exist.
- Related: [[per-slice-web-test-checkpoints]] (per-slice workflow still applies).

Recorded in [PROGRESS.md](file:///home/alsechemist/Codespace/project-wolf/PROGRESS.md) under "After 5.0a–d".
