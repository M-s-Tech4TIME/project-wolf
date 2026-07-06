---
name: deployment-aware-config-application
description: "OPERATOR DIRECTIVE (2026-07-06): config changes must be Wazuh-deployment-type-aware — all-in-one applies directly; distributed must apply/sync per component and per cluster node (ossec.conf is NOT cluster-synced; today Wolf writes the master only)"
metadata:
  type: project
---

**Directive (2026-07-06):** Wolf must dynamically apply configuration changes per Wazuh deployment type — single-server / all-in-one applies directly; a distributed deployment must "carefully send and sync across all kinds of wazuh components" (wazuh-indexer / wazuh-manager / wazuh-dashboard) according to which component the change targets.

**Why (grounded gap):** today `_ConfigChangeExecutor` writes via `PUT /manager/configuration` = the API-serving node (master) ONLY, and Wazuh cluster sync replicates rules/decoders/CDB-lists/agent-groups but NOT ossec.conf → on the operator's 3-node cluster a config change leaves the workers on the old config (worker-side analysisd/integrations keep the old behavior). rule_tuning is unaffected — `local_rules.xml` IS cluster-synced.

**How to apply:** detect deployment via `GET /cluster/status` (enabled+running vs single-node); the distributed path applies per-node via the per-node cluster configuration endpoints (`/cluster/{node_id}/configuration` — verify empirically on the live cluster FIRST, per [[scope-and-validation-discipline]]), per-node validation + rollback, one cluster restart, and the proposal/result surfaces exactly which nodes were touched. Indexer/dashboard config files live on other hosts and are UNREACHABLE via the Wazuh Server API → that half belongs to [[wolf-pack]] (Phase 12); state the boundary honestly, never pretend to have synced them. Related: [[phase-6e3-rule-tuning-web-test]].
