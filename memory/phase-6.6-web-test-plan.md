---
name: phase-6.6-web-test-plan
description: "Phase 6.6 (Superuser Wazuh mapping) web-tests + the dynamic per-org scoping model that came out of them. Category-1 UI test PASSED after 6.6-d; Category-2 functional test ran for real at 6.6-e/6.6-f against the operator's distributed cluster and passed; 6.6-f reworked per-org scoping (drop static org-id filter → optional agent.labels.group injection; probe=index read; scope from RBAC policies)."
metadata:
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

Decided 2026-06-17 with the operator. Phase 6.6 = Superuser-owned Wazuh
component mapping (ADR 0020). Two web-test categories were planned: Category-1
(UI/gating/builders/validation/hard+soft-fail, NO Wazuh) consolidated after
6.6-d, and Category-2 (real probe success + scope + chat→Wazuh) for real at
6.6-e. **The operator HAS a real distributed Wazuh** (3 indexers
192.168.250.2-4:9200, master+2 workers .5-.7:55000, 2 dashboards .8-.9).

**Status (2026-06-18):**
- **Category-1 PASSED** — operator confirmed gating/builders/validation/
  hard+soft-fail (incl. the distributed builder, after 6.6-b.1 added optional
  component names + multiple dashboards).
- **Category-2 PASSED** functionally against the real cluster: topology probe
  (3 indexers 200, master+2 workers 200, 2 dashboards **302** — redirect-to-
  login = reachable, BY DESIGN), per-org credential probe + scope, and the
  chat→per-org-creds→Wazuh path.

**6.6-f came directly out of that functional test** (operator set up real
per-org RBAC per Wazuh's "read+manage a group of agents" use case) and is
SHIPPED + live-verified. The durable model (full detail in ADR 0020's 6.6-f
addendum + CHANGELOG 2026-06-18):
- **Per-org isolation is credential-driven, not Wolf-filtered.** Each org's
  Wazuh user is scoped by Wazuh RBAC (Server-API `agent:group:<org>`) + index
  **DLS** (`match agent.labels.group:<org>` on `wazuh-alerts*`). Wolf imposes
  NO static filter by default — the credential is the boundary.
- The old static `organization_id` indexer filter was DROPPED (Wazuh alerts
  never carry it). Replaced by an OPTIONAL, opt-in `inject_group_label_filter`
  → `terms:{agent.labels.group:[labels]}` (the REAL field; multi-label), for
  credentials that AREN'T DLS-scoped. `wazuh_agent_groups` → `agent_group_labels`.
- Per-org **indexer probe** = `_count` on the index pattern (a scoped role is
  correctly denied `GET /` cluster root → was a misleading "authenticated 403").
- **Scope** comes from `GET /security/users/me/policies` (the credential's own
  RBAC, allowed for self) → the TRUE `agent:group:*` scope, NOT the incidental
  multi-group membership of its agents.

**Operator credential-isolation convention** (their explicit decision): reserve
the Wazuh **superusers** (`admin` indexer / `wazuh-wui` Server-API) for Wolf's
**install-level topology** config only; each org gets a **dedicated, scoped**
Wazuh user (`wolf-<org>`) for its per-org credentials. Matches Wolf's two
credential layers exactly.

**Remaining:** operator to confirm the new 6.6-f credentials-card behavior
(honest probe messages + scoped-group badges + the group-label checkbox), then
Phase 6.6 CLOSED. Tracked follow-up (separate slice): drop the vestigial per-org
URL columns + modernize `bootstrap_organization` + indexer-node fallback.
Per [[no-unaddressed-errors]] + [[per-slice-web-test-checkpoints]] these tests
are tracked, not skipped.
