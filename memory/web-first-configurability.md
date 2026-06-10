---
name: web-first-configurability
description: "STANDING RULE (2026-06-10) — Wolf must be fully configurable via the web interface. CLI remains available; changes made via web AND CLI must SYNC (database is source of truth, both surfaces show the same state, audit log captures both)."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

STANDING RULE (2026-06-10): the operator's design intent is that **Wolf is operable end-to-end through the web interface**. Every configuration surface that matters to the user must have a GUI; the CLI is a power-user / automation tool, not the only access path.

## The two non-negotiable properties

1. **GUI completeness** — every configurable knob is reachable from the web UI. Wolf-cert lifecycle, Wolf-database config, Wazuh component mapping, organization management, user management, role assignment, model selection, embedding provider config, RAG corpus management, wolf-pack deployment, settings of every component — all configurable from the dashboard.

2. **CLI ↔ GUI sync** — changes made via the CLI must be immediately visible in the GUI. Changes made via the GUI must be visible to the CLI. The database is the source of truth; both surfaces are views over the same state.

## The architectural pattern

```
                  ┌─────────────────────────────┐
                  │  Database (source of truth)  │
                  │  + audit log of every change │
                  └──────────────┬──────────────┘
                                 │
                ┌────────────────┴────────────────┐
                ▼                                 ▼
        ┌──────────────┐                  ┌──────────────┐
        │  Web GUI     │                  │  CLI wrappers│
        │  (read/write)│                  │  (read/write)│
        │              │                  │  per         │
        │              │                  │  [[shell-    │
        │              │                  │  wrapper-    │
        │              │                  │  required-   │
        │              │                  │  pattern]]   │
        └──────────────┘                  └──────────────┘
                ▲                                 ▲
                │   Both go through the same      │
                │   wolf-server API + DB layer.   │
                │   No "side-channel" state where │
                │   one surface knows + the other │
                │   doesn't.                      │
                └─────────────────────────────────┘
```

## What this rules out (anti-patterns)

- **Config files that the GUI can't read or write.** If a setting lives in `/etc/wolf-server/env` and the GUI can't surface its current value, that's a violation. Either move it to the DB OR have a sync layer that reads/writes the file from the API.
- **CLI-only feature flags.** Every flag must have a GUI surface (could be hidden in advanced settings, but must exist).
- **State in memory only.** If a setting can be changed via CLI but only takes effect after restart, the GUI must show the current persisted value (DB) + a "pending restart" indicator if applicable.
- **"Hidden" configuration.** Operators must NEVER need to read source code to discover a configurable.

## What this rules IN (anti-patterns to ENABLE)

- Database-backed configuration for everything that can be DB-backed (auth, RBAC, Wazuh component mapping, RAG corpora, organization metadata, user roles, model preferences, embedding provider choice, etc.).
- For file-based things (TLS certs, OS-level paths, env files): the GUI shows the CURRENT state by reading the file + has buttons to invoke the CLI tools that modify them. The CLI tools update the file + emit audit events; the GUI re-reads to show the new state.
- Audit-log every config change (regardless of CLI or GUI origin) with: who, what, when, before-value, after-value.

## What this means for the ROADMAP

This is not a single feature — it's a *posture* that affects every future slice. Going forward:

- Phase 5.4 (HTTPS + wolf-cert) — wolf-cert CLI exists; **needs a GUI counterpart** (a "Certificates" page in Superuser settings). Future slice.
- Phase 5.7 (wolf-database) — wolf-database CLI exists; **needs a GUI counterpart** for the operator (status, reconfigure UI). Future slice.
- Phase 7.5 (Central Brain memory) — needs an "Operator memory" page in user settings (right-to-be-forgotten / inspection / edit).
- Phase 9.5 (wolf-hunt) — needs its own dashboard surface; already scoped in ADR 0017.
- Phase 11.5 (wolf-den) — same.
- Phase 12 (wolf-pack) — needs deployment + management UI for the agent fleet.

## Implementation discipline

When opening any new slice that adds a configurable:

1. Design the DB schema FIRST (source of truth).
2. Design the API endpoints (GET + PUT / POST for the config) — used by BOTH the GUI and any CLI tool.
3. Build the CLI tool against the API (or a shared library).
4. Build the GUI against the API.
5. Both surfaces test against the same API contract.

When refactoring existing config:

1. Identify what's currently file-based or CLI-only.
2. Migrate the source-of-truth to the database (with a migration script).
3. Add API endpoints + GUI surface.
4. Update the CLI to use the API (or the underlying library).
5. The file (if still needed for systemd boot) becomes a downstream render of the DB.

## How to push back if asked to skip the rule

If a future request says "let's just add a CLI flag, GUI can come later" — push back with this rule. **The GUI is not a future-phase nicety; it's the canonical access path.** CLI is the auxiliary tool. New features ship with BOTH or with the GUI marked as `Phase X+1` with an explicit follow-up tracked.

Related: [[shell-wrapper-required-pattern]] (the CLI side of this duality), [[integrity-across-the-stack]] (state consistency between GUI + CLI), [[no-unaddressed-errors]] (every config change is audited regardless of origin).
