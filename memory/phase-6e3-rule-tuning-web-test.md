---
name: phase-6e3-rule-tuning-web-test
description: "PASSED (2026-06-30) live web-test of 6-e.3 (rule_tuning) — incl. a verify-bug fix found during it; real manager-global write + cluster restart"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

6-e.3 (rule_tuning) shipped 2026-06-29 (commit 6bdb011). **Live web-tested PASSED 2026-06-30** on the operator's real 3-node cluster, Tests A/B/C all green — but only AFTER a real verify bug surfaced + was fixed (commit **061d389**).

**The bug the first web-test caught (important lesson):** Test B reported "succeeded" but the rule was NOT actually disabled (DB result: `effective_level:5, matches:False`). A controlled live probe (clean rule 100700, adjust 5→7→restore) proved: the WRITE mechanism was always sound (PUT persists, validation passes, **cluster restart reloads in ~18s**, restore works). The defect was VERIFY: `GET /rules` reflects the **on-disk file immediately** (no restart needed to see it) AND returns the **original AND the `overwrite="yes"` override as SEPARATE entries** — the old verify took `items[0]` (the original, old level) → phantom success. Fix: after PUT+validate, re-read `local_rules.xml` and confirm OUR marked override block persisted (`rule_tuning.has_override`) BEFORE the restart → restore+fail honestly if not; verify surfaces `override_written`/`levels`/`target_level_in_ruleset` (no post-restart re-read — it races the restart API outage). `_resolve_rule` prefers the local_rules.xml entry (dual-file ids — 100001 existed in modesecurity_rules.xml @10 AND local_rules.xml @5, a poor first target → re-tested on clean 100700). GUI `resultDetail` now renders the rule_tuning evidence (was silently unrendered). ADR 0029 §6 updated.

**Confirmed live (2026-06-30):** forward disable 100700 → `override_written:True`, `levels:[{5,local},{0,local}]` (level-0 override IS in the ruleset), validation OK, restart issued; operator confirmed the /actions card now shows the result line. Test C restore → `override_removed:True` → original `rolled_back`. Test A (Acme/per-org) → REFUSED.

**Standing facts for rule_tuning (and reusable for 6-e.4):** positive tests need an org backed by the admin `wazuh-wui` cred (`rules:update`+`cluster:restart` on `*:*:*`; per-org `wolf-*` lack it). Approving RESTARTS the cluster (~18s, brief gap across all orgs — pick a quiet window). `GET /rules` = on-disk parse (immediate), returns original+override entries. NEXT slice: **6-e.4 (config_change)** — ossec.conf snapshot-restore, reuses migration-0017 `prior_state` + the validate→restart→rollback machinery + this authoritative-confirm pattern. Restart wolf-server via `systemctl --user restart wolf-server.service` (see [[wolf-server-restart-in-harness]]).
