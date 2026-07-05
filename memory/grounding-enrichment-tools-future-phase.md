---
name: grounding-enrichment-tools-future-phase
description: COMMITTED dedicated phase (roadmap 6.13, operator mandate 2026-07-05) — enhance + enrich Wolf's grounding across EVERY aspect so verdicts land Verified more often, accurately (verification/justification/validation); web-research evidence explicitly in scope
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

User clarified (2026-05-28) that the idea is *not* a separate "grounding tools" category — it's adding more **supporting tools** that happen to enrich Wolf's evidence dictionary. Every new tool, regardless of its main purpose, automatically expands what the grounding judge can verify against. The "dedicated phase" framing is just one of two ways to act on this; continuous evaluation of every new tool's evidence value is the other.

**UPGRADED 2026-07-05 (operator mandate, from the 6-f.3 web-test):** now a **committed, fully dedicated phase — roadmap Phase 6.13 "Grounding enrichment & verification depth"** (needs its own ADR when it opens). Trigger: correct answers built straight from official docs still landed mostly Uncertain/Not-Verified (`grounding 0✓ 2⚠ 1✗`). Goal: more **Verified** verdicts, accurately, "with proper verification, justification and validation" — never by loosening the judge. Operator-explicit addition: **web-research grounding** — the judge must verify claims against `web_search`/`web_fetch`/`web_crawl` evidence, **source-tier-aware** (official docs = strong evidence, community = weaker; untrusted-content envelope stays authoritative). Full scope in the roadmap section: web evidence tiers · more evidence-supplying tools · per-claim evidence selection (deferred from ADR 0026's pulled `cited` scope) · judge/verdict quality (multi-source corroboration, justification surfaced in chips) · a labelled calibration harness so "more green" = better calibration, not drift. Sequencing operator-driven ("we will do that later") — after the active 6-f work.

**Why:** Wolf's grounding strength is bounded by the evidence Wolf can fetch. Every general-knowledge or inferential claim with no tool/RAG source today gets a yellow Uncertain. More tools with citeable output → more Verified verdicts → stronger user trust.

**How to apply:**
- **Continuously, in any phase:** when scoping a new tool, evaluate it on "what claims does this make verifiable?" alongside the tool's main purpose. Prefer tools whose output is structured + cite-able.
- **As the dedicated phase (6.13):** focused work prioritising tools by evidence value. Candidate list: `get_agent_details`, `lookup_ip_reputation`, `get_attack_technique` (MITRE), `get_cve_details`, `quote_runbook` (exact-passage retrieval with line numbers), expanded `get_rule_definition` coverage — plus the web-evidence + per-claim + judge-quality items above.
- Each tool organization-scoped via the existing OrganizationScopedQueryBuilder/PgvectorKnowledgeStore patterns. External feeds (IP rep, CVE) need an API-key plumbing pattern that respects the secrets backend.
- Update ADR 0015 and the validator prompt once the new evidence-source vocabulary stabilises — the judge needs to know these new source types exist.
- Related: [[web-research-phase]], [[grounding-execution-modes]], [[web-research-as-universal-power]], [[per-slice-web-test-checkpoints]].

Recorded in the roadmap (Phase 6.13) and the ADR 0032 2026-07-05 addendum.
