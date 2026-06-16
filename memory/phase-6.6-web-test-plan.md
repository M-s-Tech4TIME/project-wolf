---
name: phase-6.6-web-test-plan
description: "Phase 6.6 (Superuser Wazuh mapping) deferred web-tests + the operator HAS a real Wazuh to point Wolf at. Category-1 UI test (gating/builder/validation/hard+soft-fail) is consolidated AFTER 6.6-d; Category-2 functional test (real probe success + scope + chat→Wazuh) runs FOR REAL at 6.6-e."
metadata:
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

Decided 2026-06-17 with the operator (who flagged that I'd been deferring the
Phase 6.6 web-tests). Phase 6.6 = Superuser-owned Wazuh component mapping
(ADR 0020); backend layers 6.6-a (install topology) + 6.6-c (per-org creds)
and UI 6.6-b (install topology page) are SHIPPED + CI-green.

**Two categories of web-test:**

- **Category 1 — UI / behaviour, NO Wazuh needed (testable now):** Superuser-
  only gating; the single/distributed topology builder (6.6-b) + the per-org
  credentials tab (6.6-d); client-side validation; the **HARD-fail** path
  (point "Test & save" at a bogus/unreachable URL → guided 400 naming the
  failing endpoint(s), nothing saved); and the **SOFT-fail** path (per-org
  creds persist even on probe failure → `validated_at` null + warning + audit
  row; "omit password ⇒ keep existing"; rotation log). **Operator decision:
  do ONE consolidated Category-1 web-test AFTER 6.6-d ships** (test 6.6-b +
  6.6-d together — less context-switching). I prep a clean state + hand off.

- **Category 2 — functional success path, needs a real Wazuh:** real probe
  SUCCESS (reachable indexer/manager/dashboard + auth), the real **scope
  summary** (agent/group counts), and end-to-end **chat → per-org creds →
  real Wazuh data** (6.6-e's exit criterion).

**The operator HAS a Wazuh instance available** to point Wolf at (confirmed
2026-06-17) — so the Category-2 functional test runs FOR REAL at **6.6-e**,
not indefinitely deferred. (This dev env had no Wazuh in `.env` at the time —
0 topology rows, 2 stale `organization_wazuh_configs` rows; the operator will
provide the target when 6.6-e is web-tested.)

Per-slice **self-validation** (tsc/eslint, route 200, 401-gating, the full
backend gate) still happens every slice — this note is about the **operator
manual tests**. Per [[no-unaddressed-errors]] + [[per-slice-web-test-checkpoints]]
these tests are **tracked, not skipped**. The same-network-gate (6.5-h.2)
deferred web-test [[same-network-gate-deferred]] can reuse this
Wazuh-available window if convenient.
