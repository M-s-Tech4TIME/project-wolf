# 0029 — Additional action classes (agent_action / rule_tuning / config_change) (Phase 6-e)

**Date:** 2026-06-29
**Status:** accepted — implementing across **6-e.1 → 6-e.4**. 6-e.1 generalizes the
gateway to be **per-class** (active-response refactored onto the registry, zero
behaviour change); 6-e.2 adds `agent_action`; **6-e.3 adds `rule_tuning`** (built,
unit-tested — §6 below); `config_change` (6-e.4) is next.

## Context

ADR 0025 established the capability-driven propose→approve→execute→verify→audit
pipeline and named four action classes; 6-a→6-d shipped **active-response** in
full (incl. reversal + provenance recall + timed auto-reversal, ADR 0028). This
ADR extends the pipeline to the remaining three, **reversal-aware from day one**,
each still capability-bounded + human-approved + audited (`unrestricted ≠
unsafe`). The gateway was AR-hardcoded in a few spots; we de-hardcode it into a
small per-class registry rather than branching everywhere.

## 1. Per-class scoping model (grounded in a live RBAC probe)

`GET /security/users/me/policies` on the live cluster (2026-06-29) shows the
classes split by **blast radius**, and the existing capability gate (ADR 0025)
already enforces it — Wolf offers a class only if the credential's RBAC permits:

| Class | Wazuh RBAC action(s) | Scope | Held by |
|---|---|---|---|
| `active_response` | `active-response:command` | agent | per-org + admin |
| `agent_action` | `agent:modify_group` / `agent:restart` / `agent:reconnect` / `agent:upgrade` | agent | restart per-org; modify_group admin/Superuser |
| `rule_tuning` | `rules:update` / `decoders:update` / `lists:update` | **manager-GLOBAL** | admin/Superuser only |
| `config_change` | `manager:update_config` / `cluster:update_config` | **manager/cluster-GLOBAL** | admin/Superuser only |

**Consequence:** `rule_tuning` / `config_change` affect *every* org on a shared
manager → they are effectively **Superuser-scoped** (a per-org credential simply
won't hold the RBAC action, so Wolf won't offer it). This is correct behaviour,
not a special case — the capability model handles it. Severity is set
accordingly (manager-global writes are high/critical).

## 2. Two reversal models

ADR 0028 found the Server API **cannot** dispatch an active-response `delete`, so
AR's physical reversal is **wolf-pack-bound** (record now, host-remove later).
The new classes are different — they are **API-executable both ways**, so their
undo runs *for real, now*:

- **API-inverse** (`agent_action` group moves): the reverse is the inverse
  operation — `assign_group` ↔ `remove_group` (`PUT`/`DELETE /agents/{id}/group/{g}`).
- **Snapshot-restore** (`rule_tuning`, `config_change`): capture the prior state
  (the rule file / ossec.conf) *before* the write; the reverse `PUT`s the
  snapshot back.

So 6-d's reversal **linkage + provenance recall generalize unchanged**
(`reverses_proposal_id`, `find_active_action`), but the reversal **executor is
per-class**: wolf-pack-bound (AR) vs API-inverse/snapshot-restore (the rest). An
API-executable reversal, once verified, flips the original to `rolled_back`
(AR's stays `succeeded` until wolf-pack confirms).

## 3. The generalization (6-e.1 — registry, zero behaviour change)

De-hardcode the AR-only assumptions into a per-`action_class` registry; AR is
refactored onto it and the existing suite stays green (the shape is validated by
agent_action landing immediately after, so it is not speculative):

- **validator** (`gateway/validator.py`): `validate_proposal` dispatches by
  `action_class` to a registered structural validator (AR's logic →
  `_validate_active_response`).
- **severity** (`gateway/proposals.py`): `compute_severity` per-class (base +
  context escalation) instead of an `active_response`-only branch.
- **active-action finder**: `find_active_block` → `find_active_action(db, org,
  action_class, matcher)` for provenance recall (AR keeps an srcip/username
  matcher; `find_active_block` delegates to it).
- **executor** (`gateway/executors.py`, NEW): a per-class executor exposing
  `build_forward(proposal, ctx)` and `build_reverse(proposal, ctx)` → the
  `(freshness, perform, verify)` callables `execute_proposal` already consumes.
  `ctx` carries the read + bounded-write Wazuh clients + capabilities + db. The
  API `approve` handler dispatches by `action_class`; `execute_proposal` (the
  engine) is untouched.
- **capability map** (`wazuh/capabilities.py`): `WOLF_ACTION_CLASS_RBAC` maps a
  class → a **set** of RBAC actions (offer the class if the credential holds any).
- **bounded write surface** (`wazuh/server_api.py`): each class adds named,
  capability-checked write methods to `WazuhServerApiActionClient` (no generic
  put/post — the whitelist discipline of ADR 0025 is preserved).

## 4. Decisions

- **Build order:** agent_action → rule_tuning → config_change (lowest → highest
  blast radius; operator-chosen 2026-06-29).
- **agent_action v1 = group management** (`assign_group` / `remove_group`) — the
  reversible, agent-scoped showcase (quarantine into an `isolated` group, move
  back). `restart` is already AR's `restart-wazuh`; `reconnect`/`upgrade`
  (non-reversible) are follow-ons.
- **Snapshot store** (rule_tuning/config_change) = a prior-state column added by
  **migration 0017** (6-e.3), reused by config_change.
- **Manager-global writes require operator go-ahead** in testing (shared-manager
  blast radius); the approval gate + strong pre-write validation are mandatory.

## 5. Out of scope (tracked)
- agent `reconnect`/`upgrade`/`uninstall`/`delete`; decoders + CDB-lists tuning
  (same shape as rule_tuning); section-level config diffing UI; auto-execution
  (Phase 13); the LLM-as-judge intent-alignment validator (ADR 0017 / Phase 7.5).

## 6. rule_tuning v1 — as built (6-e.3)

**Operator-scoped decisions (2026-06-29):**
- **Fine-tune EXISTING rules only** — `disable_rule` (set level 0) + `adjust_level`
  (set an explicit 0..16 level). Authoring net-new detection rules from scratch is
  a deliberate follow-on (it needs the deep-think / stronger-model validator —
  ADR 0017 / Phase 7.5), not v1.
- **`local_rules.xml` only** — Wolf writes the single canonical custom file via an
  `overwrite="yes"` override; it never touches the stock `ruleset/rules/` (Wazuh
  overwrites those on upgrade). Tuning a *stock* rule is also an override here
  (Wazuh's recommended pattern). Arbitrary `etc/rules/*.xml` is a follow-on.
- **Auto-apply** — rules don't hot-reload, so the action genuinely applies the
  change: snapshot → PUT → validate → (auto-rollback if invalid) → **authoritative
  confirm** → **cluster restart**. The approver consents to the restart (a brief,
  manager-global alert-processing gap) by approving the action ("what you see is true").

**Authoritative verify (corrected 2026-06-30 after the first live web-test).** The
first cut verified by reading the rule's level via `GET /rules` and taking
`items[0]` — but a live probe revealed two facts: (1) `GET /rules` reflects the
**on-disk** file *immediately* (no restart needed to see it); (2) for an
`overwrite="yes"` rule it returns the **original AND the override as separate
entries**, so `items[0]` was the *original* (old level) → the action reported a
phantom "succeeded, matches:False" while the override had in fact been written. The
fix: after PUT+validate, the executor **re-reads `local_rules.xml` and confirms our
marked override block actually persisted** (`has_override`) BEFORE issuing the
restart — if it didn't persist, restore + fail honestly. Verify no longer re-reads
after the restart (that races the brief restart-induced API outage); it surfaces
the pre-restart evidence (`override_written`, the rule's parsed `levels`,
`target_level_in_ruleset`). A rule id defined in **multiple files** resolves to the
`local_rules.xml` entry for the override source. Live-measured ruleset reload after
the cluster restart: **~18s**.

**Override construction (string-based — `local_rules.xml` is a multi-root
fragment, not a single-root XML doc):** the override copies the rule's exact inner
body from its source file (`GET /rules/files/{file}?raw=true`) and changes only
`level` + adds `overwrite="yes"`, so matching conditions are preserved. Each
override lives in a marked `wolf_tuning` group; a re-tune of the same id replaces
it (idempotent, never a duplicate sid).

**Reversibility (snapshot-restore):** `build_forward` captures `local_rules.xml`
into the **`prior_state`** column (migration 0017) at execute time, before the
write; `restore_rules` reverses by PUTting that snapshot back (validate → restart)
and tags the result completed, so `complete_api_reversal` flips the original to
`rolled_back` (a real undo, API-executable — not wolf-pack-bound).

**Safety:** `GET /manager/configuration/validation` is the correctness gate — a
non-compiling edit is auto-rolled-back (prior file restored) and never applied, so
a bad rule can't break the manager. Capability-gated on `rules:update` +
`cluster:restart` (admin-only — empirically: `wolf-acme` holds neither, the admin
`wazuh-wui` holds both on `*:*:*`). The live web-test (a real manager-global write)
requires explicit operator go-ahead.
