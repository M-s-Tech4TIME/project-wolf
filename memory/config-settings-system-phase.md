---
name: config-settings-system-phase
description: "Phase 6.10 (planned, operator-prompted 2026-06-16): the Superuser config-settings SYSTEM that implements ADR 0019 — DB-source-of-truth runtime config synced across OS terminal/env ⇄ Wolf CLI ⇄ Web Settings GUI, Superuser-only, audited. Today config is env-only (no settings table / API / config CLI / Settings page). First consumer: the same-network gate toggle."
metadata:
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

PLAN (Phase 6.10, prompted 2026-06-16 when the same-network gate
[[same-network-gate-deferred]] needed a GUI toggle): build the runtime
config-settings system that **ADR 0019** (web-first-configurability) mandates
but Wolf doesn't have yet.

**Current state (the gap):** config is **env-only** — `wolf_server/config.py`
pydantic `Settings`. There is **no** DB settings table, **no** config API,
**no** config CLI, and the dashboard `/settings` area is only `access` + `users`
(no general Settings page). So "add a toggle in the Superuser Settings page,
synced with CLI + terminal" is not a button — it needs this substrate first.

**What 6.10 builds:**
- **DB as source of truth** for operator-settable knobs + a config API.
- A **Superuser Settings GUI page** (the dashboard surface).
- A **Wolf config CLI** (shell-wrapper pattern, [[shell-wrapper-required-pattern]]).
- All three surfaces — OS terminal/env ⇄ CLI ⇄ Web-GUI — stay **identical +
  synced** (DB is the truth; each is a view), every change **audited** (ADR 0019).
- **Authorized Superuser-only** per [[superuser-config-authority]].

**First consumer:** the **same-network gate** on/off toggle (the gate shipped
env-only + default-OFF in 6.5-h.2; this turns it into a synced Superuser
switch — the toggle's sole job is enable/disable the gate). Other env knobs
migrate in per ADR 0019's catalogue.

**Adjacent follow-up:** **per-org trusted networks** (each org's own CIDRs) —
the MSSP-correct form of the same-network gate; open question whether
Superuser-set or org-admin-set.

ADR 0019 already governs the design; a focused implementation ADR can follow at
phase-open if the data model warrants. Related: [[web-first-configurability]],
[[superuser-config-authority]], [[same-network-gate-deferred]],
[[shell-wrapper-required-pattern]].
