---
name: runbook-authoring-and-actionable-runbooks
description: Future phase — admin-facing markdown editor for runbooks (organization + shared) and the path to runbook-prescribed propose-actions
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

User proposed (2026-05-28) a dedicated **Knowledge Management** phase sitting between Phase 5 (Organizations + RBAC) and Phase 6 (Propose tools + approval gateway). Three parts, ordered:

## (A) Admin UI for runbook CRUD
- Web page (org-admin role; superuser can also edit shared global corpora `wazuh_doc` + `attack`).
- Markdown editor — likely **MDXEditor** (Tailwind-themable, modern, lightweight). TipTap with a markdown extension is the fallback.
- CRUD against the existing `knowledge_chunks` table: organization-scoped per RBAC.
- Auto-chunk on save (current store accepts pre-chunked input; a server-side splitter respecting markdown structure goes here).
- Re-embed on save: re-run both Ollama embedders, refresh `embedding`, `embedding_v2`, and let Postgres re-compute `content_tsv`. The `upsert` in [PgvectorKnowledgeStore](file:///home/alsechemist/Codespace/project-wolf/services/orchestrator/app/knowledge/store.py) already does the heavy lifting; an "update" path that deletes old chunks for the same logical doc, then inserts the new ones, is the cleanest semantics (each saved markdown doc = a logical bundle of one or more chunks with a shared `chunk_metadata.doc_id`).
- Structured metadata exposed in the editor UI: title, rule_id, technique, plus a new `action_type` tag for propose-action mapping (see C).

## (B) Precision retrieval for runbooks (overlaps with grounding-enrichment-tools)
- The current hybrid retrieval (BM25 + nomic-embed-text + nomic-embed-text-v2-moe via RRF) already finds the right runbook for a topic. Two enhancements:
  - **Tag-filtered retrieval**: `query_runbook(rule_id=..., technique=...)` already exists; surface it in the editor so authors can attach explicit tags that retrieval honours.
  - **`quote_runbook(query)`** (proposed in [[grounding-enrichment-tools-future-phase]]): returns exact passages with line numbers so the grounding judge can match claim-to-runbook text precisely. This is what closes the loop with the chips.

## (C) Runbook-prescribed propose-actions (ties to Phase 6)
**Hard safety rule, not negotiable:** runbook step → Wolf **proposes** → human **approves** → orchestrator executes. Wolf never auto-executes. The orchestrator has a CI check enforcing no `execute_*` tools today (doc-04 safety + the existing CI workflow); the propose-and-approve gateway is the legitimate path.

Mechanism:
- Runbook markdown can declare action lines (e.g. fenced block tagged ` ```action` or front-matter `actions: [block_ip, isolate_host]`).
- The retrieval pipeline parses these out into structured `RunbookAction` records associated with the chunk.
- When the agent loop is invoked in a future "propose mode" (Phase 6), Wolf reads the runbook for the current incident and turns each `RunbookAction` into a draft `ProposedToolCall` with **provenance** back to the runbook chunk + line number. The analyst sees: *"This action comes from page X of your `[ACME SOC] SSH brute-force runbook`, line 4: 'Block source IP at the perimeter for 24 h.'"*
- Approval gateway dispatches the actual Wazuh Server API call (e.g. POST /active-response).

## Suggested ordering
| When | Phase | Why this order |
|---|---|---|
| Now → next | 5.0c-d (UI overhaul + theme) | Already in flight |
| Phase 5 | Organizations + RBAC | Defines *who* can author |
| **Phase 5.5 (new) — Knowledge management** | This idea | Needs RBAC for authoring; enables the next two |
| Phase 5.6 (existing) — Grounding-enrichment tools | `quote_runbook` + structured tags | Uses the now-richer runbook surface |
| Phase 6 — Propose tools + approval | Runbook-prescribed actions become first-class proposals | Closes the loop |

## Why this is high-value
Today the only way to update a runbook is to edit Python in `seed_dev_knowledge.py` and re-seed — analysts can't author. Closing that gap unlocks the rest of the chain: every Slice 5.0b.* grounding improvement is bottlenecked on *how good the runbooks are*, and that bottleneck is currently "the developer has to ship code." Giving org admins the editor moves the ceiling.

Recorded in [PROGRESS.md](file:///home/alsechemist/Codespace/project-wolf/PROGRESS.md) under "After 5.0a–d". Related: [[grounding-enrichment-tools-future-phase]], [[per-slice-web-test-checkpoints]].
