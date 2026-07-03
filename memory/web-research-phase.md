---
name: web-research-phase
description: "PLANNED PHASE (2026-07-03, operator-directed from the 6-e.4 web-test): Wolf gains internet research 'like Claude' — provider-agnostic web_search/web_fetch, SearXNG self-hosted DEFAULT (pluggable, privacy-first), docs-first→community fallback, citations into the evidence panel; converges with config-authoring generalization (edit ANY ossec.conf incl. repeated/<integration>, free-form, research→confirm→validate→propose). ADR pending; sequenced AFTER the 6-e.4 config fixes (DONE, commit ecc3562)."
metadata:
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

**Operator directive (2026-07-03, out of the 6-e.4 config_change web-test).** Two
asks that converge into ONE capability — **Wolf as a research-capable Wazuh expert**:

1. **Edit ANYTHING from a description.** config_change v1 is deliberately narrow
   (7 allowlisted single-instance sections; repeated/merge-semantic sections like
   `<integration>` excluded; no free-form). Operator wants Wolf to action any
   request — precise ("here's the exact block, apply it") OR descriptive ("harden
   FIM, figure out where/how") — via research → understand → **confirm with the
   user** → **dry-run validate** (Wolf already has `/manager/configuration/validation`
   pre-restart) → propose. "Robust, redundant, sophisticated."
2. **Internet search like Claude.** Search the web for anything Wazuh (rules,
   decoders + references, config changes, blog posts, community guidelines, docs —
   nothing left behind), **official docs FIRST** (documentation.wazuh.com →
   wazuh.com/blog → github.com/wazuh), broaden to community on a miss, answer WITH
   references. Studied Anthropic's mechanism (2026-07-03): model-DECIDED agentic
   tool, progressive multi-search (earlier results refine later queries), `web_fetch`
   for depth, first-class citations, `allowed_domains`/`blocked_domains`.

**Design decisions (operator-chosen).**
- Backend = **SearXNG self-hosted, DEFAULT, behind a pluggable adapter** (mirrors the
  model-provider abstraction). Zero query egress → aligns with the local-Ollama
  no-prompt-leak posture + MSSP tenant isolation; no key, no per-query cost. Hosted
  options remain per-org selectable (Brave = ZDR + free 2k/mo + fastest 2026 bench;
  Tavily = AI-optimized extraction, ~$8 CPM). NOT the Anthropic server-side
  web_search tool (Ollama-incompatible — Wolf must stay provider-agnostic).
- Two new provider-agnostic tools `web_search` + `web_fetch` the agent loop chains;
  results flow into Wolf's EXISTING evidence/grounding citation panel (a major
  grounding-enrichment source → more Verified verdicts). Fetch is SSRF-guarded
  (block internal IPs, http/https only, size/timeout caps — security-sensitive).
- Converges with **config-authoring generalization**: any section incl. repeated
  (`<integration>`/`<localfile>`/`<command>`) via block-identity, free-form
  authoring; safety = confirm + dry-run validate + snapshot-restore reversal (built).

**Sequencing.** Config fixes FIRST (DONE — commit ecc3562, CI green: reformatting-
tolerant persist check + configurable proposal TTL, 30 min default). THEN this phase
as its own ADR (next free number, ~0032). Not started beyond this decision.

**Reusable reference from the 6-e.4 web-test.** (1) Wazuh **re-serialises ossec.conf
on write** (re-indents to house style) → verify config/rule persistence
STRUCTURALLY (whitespace-tolerant compare / `has_override`), NEVER by literal
substring — that was the `<sca>` "block did not persist" false-negative (rule_tuning
avoided it via `has_override`, which is why rules passed on the same 3-node cluster).
(2) "SCA-only" was **model steering, not a code limit** (7 sections allowlisted).
(3) The 30-min approval TTL is `PROPOSAL_TTL_SECONDS` (`Settings.proposal_ttl_seconds`),
staleness still guarded independently at execute by each class's freshness re-check.

Links: [[grounding-enrichment-tools-future-phase]], [[runbook-authoring-and-actionable-runbooks]],
[[phase-6e3-rule-tuning-web-test]], [[grounding-execution-modes]], [[config-settings-system-phase]].
