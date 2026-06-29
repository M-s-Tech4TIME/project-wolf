---
name: phase-6e3-rule-tuning-web-test
description: "PENDING live web-test checkpoints for 6-e.3 (rule_tuning) — operator will test later; real manager-global write, cluster restart"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

6-e.3 (rule_tuning) shipped 2026-06-29 (commit 6bdb011, CI green, 695 tests / 0 skip, wolf-server restarted — `propose_rule_tuning` live). The **live web-test is PENDING** — operator (abid) will run it later. Operator pre-cleared BOTH the Superuser/admin credential use AND the real manager-global write.

**⚠ Approving a rule_tuning RESTARTS the Wazuh manager cluster** (brief alert-processing gap across ALL orgs) — pick a quiet window. Per-org `wolf-*` creds lack `rules:update`; positive tests need an org backed by the **admin `wazuh-wui`** credential (which holds `rules:update` + `cluster:restart` on `*:*:*` — verified by live RBAC probe). Suggested test rule: **100001** (sample custom sshd rule, level 5, low impact) — but any rule works.

**Checkpoints to run:**
- **Test A — capability gate (negative):** as an Acme user (per-org `wolf-acme`), ask "disable rule 100001" → expect REFUSED ("lacks rules:update … Superuser-scoped"); nothing queued.
- **Test B — tune + apply + verify (forward):** in the org backed by `wazuh-wui`, ask "disable rule 100001" (or "set rule 100001 to level 3") → pending card in /actions (severity high) → approve → succeeded; Evidence shows effective_level/matches/validation OK/restart issued; confirm `local_rules.xml` has a `wolf_tuning` override for 100001 (Wazuh dashboard → Management → Rules).
- **Test C — undo (snapshot-restore reversal):** ask "restore rule 100001" → Wolf recalls the tuning reason + queues a linked restore card → approve → restore succeeds, ORIGINAL flips to `rolled_back`, `local_rules.xml` back to pre-tuning state.

**Known honesty note:** the new ruleset is live only once the cluster restart completes (a few seconds); verify reads the rule definition — if `matches` shows the old level momentarily, wait for the restart and re-check. Empirical Q to confirm during the test: does `GET /rules` reflect the on-disk file immediately (then tighten verify to require matches) or the loaded state (post-restart)?

After the web-test passes, NEXT slice is **6-e.4 (config_change)** — ossec.conf snapshot-restore, reuses migration-0017 `prior_state` + the validate→restart→rollback machinery. Builds in the same [[grounding-execution-modes]]-style honest-boundary spirit. Restart wolf-server via `systemctl --user restart wolf-server.service` (see [[wolf-server-restart-in-harness]]).
