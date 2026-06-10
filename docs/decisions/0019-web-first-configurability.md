# ADR 0019 — Web-first configurability mandate (GUI ↔ CLI sync)

**Status:** PROPOSED (2026-06-10)
**Authors:** Wolf Maintainers
**Related:** ADR 0017 (Wolf Central Brain — new memory surfaces need GUI),
ADR 0018 (Bootstrap Superuser + RBAC + Login UX — defines WHO can
configure WHAT for users + roles), ADR 0020 (Superuser-owned Wazuh
component mapping — the Wazuh-specific WHAT), `shell-wrapper-required-
pattern.md` memory (the CLI half of the GUI↔CLI duality),
`web-first-configurability.md` memory (the standing rule this ADR
formalizes)
**Supersedes:** None — establishes a forward-looking discipline

---

## Context

Operator direction (2026-06-10): **Wolf must be operable end-to-end
through the web interface.** Every configurable knob — current and
future — must be reachable from the dashboard. The CLI remains as a
power-user / automation / recovery tool, NOT as the only access path
for any feature.

A second non-negotiable property: **GUI ↔ CLI sync.** Changes made via
the CLI must be immediately visible in the GUI. Changes made via the
GUI must be visible to the CLI. The database is the source of truth;
both surfaces are views over the same state.

Today's Wolf has CLI surfaces for `wolf-cert` (HTTPS/mTLS lifecycle),
`wolf-database` (Postgres lifecycle), `bootstrap_tenant` (org + user
seeding), `wolf-database init/start/stop/status/reconfigure`, etc. None
of these have GUI counterparts. Operators must SSH into the wolf-server
host to do basic admin. That's wrong for v1.

---

## Decision

This ADR commits to two architectural rules:

### Rule 1 — GUI completeness

Every configurable Wolf knob has a GUI surface. No exceptions for
"power-user" features. The GUI may organize features differently from
the CLI (Settings → Advanced for things only operators rarely touch),
but the feature must be REACHABLE from the GUI.

Affected surfaces today (the catalog this ADR commits to delivering):

| CLI today | Future GUI counterpart | Owner role | Phase |
|---|---|---|---|
| `wolf-cert init / status / export-ca / add-host / renew / revoke` | "Certificates" page in Superuser settings | Superuser | Future slice (post-6.5) |
| `wolf-database init / start / stop / status / reconfigure` | "Database" page in Superuser settings | Superuser | Future slice |
| `bootstrap_tenant.sh` (→ `bootstrap_organization.sh`) | "Organizations" page in Superuser settings | Superuser | Phase 6.5-e (per ADR 0018) |
| `bootstrap_superuser.sh` recovery | "Superuser password rotation" page | Superuser | Phase 6.5-a |
| Per-org Wazuh component mapping | "Wazuh" page in Superuser settings | Superuser | Phase 6.6 (per ADR 0020) |
| Per-org RAG corpus management | "Knowledge" page in Engineer settings | Engineer | Future slice (depends on Phase 7.5 memory + Phase 10 knowledge growth) |
| Per-org model + embedding selection | "AI Models" page in Engineer settings | Engineer | Future slice |
| Per-org wolf-pack deployment | "Wolf Pack" page in Engineer settings | Engineer | Phase 12 |
| Org-Admin user management | "Users" page within each org | Admin | Phase 6.5-f (per ADR 0018) |
| Operator memory inspect / delete | "My memory" page in user settings | User (self only) | Phase 7.5 (per ADR 0017) |
| All `make smoke-*` targets | (none — CI-only; no GUI counterpart needed) | n/a | n/a |

Anything added in a future phase implicitly inherits this rule.

### Rule 2 — Database as source of truth

The DB holds the canonical state. CLI and GUI are equivalent VIEWS over
the same state.

```
                  ┌─────────────────────────────────────┐
                  │  Database (canonical state)         │
                  │  + audit_events row for every change │
                  └──────────────────┬──────────────────┘
                                     │
                          ┌──────────┴──────────┐
                          │  wolf-server API     │
                          │  (the SINGLE write   │
                          │   path for both      │
                          │   surfaces)          │
                          └──────────┬──────────┘
                                     │
                ┌────────────────────┴────────────────────┐
                ▼                                         ▼
        ┌──────────────┐                          ┌──────────────┐
        │  Web GUI     │                          │  CLI wrappers│
        │  (read/write │                          │  (read/write │
        │   via API)   │                          │   via API)   │
        └──────────────┘                          └──────────────┘
```

Key consequences:

- **The CLI talks to the API**, not directly to the database. (Today,
  some CLI tools touch files or the DB directly — those need to be
  refactored to go through the API. The shell-wrapper pattern stays;
  the wrapper just calls the API instead of mutating files.)
- **Both surfaces emit identical audit events.** A config change shows
  up in the audit log with the same shape regardless of whether GUI or
  CLI triggered it — only the `source` field differs (e.g.,
  `source: dashboard` vs `source: cli`).
- **No GUI-only or CLI-only side effects.** If the CLI sets a flag that
  the GUI doesn't know about, that's a bug. Same the other way.
- **State changes that require restart** are reflected via a "pending
  restart" indicator visible to BOTH surfaces.

---

## Anti-patterns this ADR rules OUT

- **Config files the GUI can't read or write.** If a setting lives only
  in `/etc/wolf-server/env`, the GUI must either surface it via a
  read-write API OR the setting moves into the DB. **File-based config
  is a SHADOW state if the GUI can't see it.**
- **CLI-only feature flags.** Every flag has a GUI surface, even if
  hidden in Settings → Advanced.
- **State in memory only.** If a setting takes effect only after restart,
  the GUI must show (a) the persisted value (DB) and (b) a "pending
  restart" indicator. CLI same view.
- **Hidden configuration.** Operators must never need to read the
  source code to discover a configurable. The GUI is the discovery
  surface.

---

## Anti-patterns this ADR rules IN (enables)

- **Database-backed configuration for everything that CAN be DB-backed.**
  Auth, RBAC (per ADR 0018), Wazuh component mapping (per ADR 0020), RAG corpora,
  organization metadata, user roles, model preferences, embedding
  provider, etc.
- **For file-based things** (TLS certs, OS-level paths, env files): the
  GUI shows current state by reading the file via an API endpoint + has
  buttons to invoke the CLI tools that modify the file. The CLI tools
  update the file + emit audit events; the GUI re-reads to show the new
  state. The file is a downstream render of the DB where possible.
- **Audit-log every config change** (regardless of CLI or GUI origin)
  with: who (user), what (operation), when (timestamp), before-value,
  after-value.

---

## How file-based configuration works under this ADR

Some Wolf state lives in files for legitimate reasons:

- **TLS certs** — must be on disk because systemd-managed services read
  them at startup.
- **`/etc/wolf-server/env`** — systemd EnvironmentFile, must be in the
  file system.
- **wolf-database's `postgresql.conf`** — Postgres reads it.

The DB can't BE these files. So the architectural pattern is:

```
                ┌─────────────────────────┐
                │  Database               │
                │  (canonical config row) │
                └──────────┬──────────────┘
                           │
                           │  (on every change)
                           ▼
                ┌─────────────────────────┐
                │  File-renderer          │
                │  (a service or job that │
                │   writes the file from  │
                │   the DB state)         │
                └──────────┬──────────────┘
                           │
                           ▼
                ┌─────────────────────────┐
                │  File on disk           │
                │  (read by systemd /     │
                │   postgres / etc.)      │
                └─────────────────────────┘
```

When the GUI changes a setting:
1. PUT to the API
2. API writes to DB
3. API triggers file-renderer
4. File-renderer writes the file
5. GUI shows "pending restart" if applicable
6. Operator (or auto-restart hook) restarts the service

When the CLI changes a setting:
- The CLI wrapper calls the API (Rule 2)
- Same sequence; identical audit event

The file on disk is the DOWNSTREAM render. The DB is the SOURCE OF TRUTH.
If the file and DB ever diverge (operator hand-edited the file), the
file-renderer overwrites the file at next change. We don't try to
preserve hand-edits. (Operators can always make the change in the GUI/CLI
and it'll land correctly.)

---

## Out of scope for this ADR

- **The detailed UI/UX design** for each of the catalog pages above.
  Each gets its own slice in the relevant phase with its own UI mock
  + iteration.
- **MFA + OIDC SSO integration** — separate auth concern; addressed
  partially in ADR 0018 future scope.
- **Internationalization** of GUI labels — future scope.
- **GUI theming / dark mode** — future scope (Slice 5.0c shipped the
  Platinum palette).
- **CLI scripting language** — if Wolf ever ships a declarative config
  language (e.g., YAML for batch admin), that's a separate ADR.

---

## Implementation discipline (going forward)

When opening any new slice that adds a configurable:

1. **Design the DB schema FIRST** (source of truth)
2. **Design the API endpoints** (GET + PUT / POST for the config) —
   used by BOTH the GUI and any CLI tool
3. **Build the CLI tool against the API** (or a shared library that
   wraps the API)
4. **Build the GUI against the same API**
5. **Both surfaces test against the same API contract**
6. **Audit event schema declared in the API endpoint definition**

When refactoring existing configurable state:

1. **Identify what's currently file-based or CLI-only**
2. **Migrate the source-of-truth to the database** (with a migration
   script for any existing operator state)
3. **Add API endpoints + GUI surface**
4. **Update the CLI to use the API**
5. **The file (if still needed for systemd boot) becomes a downstream
   render of the DB**

---

## Existing CLI surfaces — refactor scope

The following existing CLI tools currently touch files or the DB
directly. Each needs a refactor slice to move them to the API:

| Tool | Today | Refactor target | Phase |
|---|---|---|---|
| `wolf-cert init` | Writes files at `.local/certs/` (dev) or `/etc/wolf/certs/` (prod) | Call API → API writes files → API records cert metadata in DB | Future slice (post-6.5; not blocking) |
| `wolf-database init/start/stop/status/reconfigure` | Direct pg_ctl invocation + file writes | Call API → API records config in DB → file-renderer produces postgresql.conf + pg_hba.conf | Future slice |
| `bootstrap_tenant.sh` (→ `bootstrap_organization.sh`) | Direct DB inserts via SQLAlchemy | Call API endpoint that does the inserts; CLI is a thin wrapper | Phase 6.5-e (per ADR 0018) |
| `bootstrap_superuser.sh` | (new in Phase 6.5-a) | Build correctly from the start: shell wrapper → API → DB | Phase 6.5-a |

This is substantial follow-up work. The ADR does NOT mandate doing it
all at once — refactors land slice-by-slice over multiple phases. But
the discipline is set: **new code follows Rule 2 from day one; existing
code gets migrated incrementally**.

---

## Implementation sequencing

- **Phase 6.5** (per ADR 0018) — implements the API + GUI for Bootstrap
  Superuser + RBAC + Login + Org management + User management. New code,
  follows Rule 2 from start.
- **Phase 6.6** (per ADR 0020) — implements the API + GUI for
  Superuser-owned Wazuh component mapping (install-level topology +
  per-org credentials). Sequenced after 6.5 so the Superuser + RBAC
  model is in place. New code, follows Rule 2 from start.
- **Phase 5.X-style settings slices** (post-6.5, ahead of v1.0) —
  refactor existing wolf-cert + wolf-database CLI tools to go through
  the API + add their GUI counterparts. Each is its own discrete slice.
- **Phase 7.5** (per ADR 0017) — Central Brain memory + thinking + 
  self-validation. New code, follows Rule 2 from start. Includes the
  "My memory" GUI surface per the catalog above.
- **Phase 9.5 / 11.5 / 12** — wolf-hunt / wolf-den / wolf-pack each get
  their own dashboard surfaces; built from the start to Rule 2.

---

## Open architectural decisions

1. **Restart semantics for file-renderer changes** — Auto-restart
   wolf-server when env changes? Or require manual operator restart?
   (Auto-restart is operator-unfriendly during active investigations;
   manual is safer but adds a "pending restart" step to every config
   change.)
2. **API endpoint naming convention** for settings — `PUT
   /api/v1/settings/wazuh-mapping` vs `PUT /api/v1/superuser/wazuh-
   mapping` vs `PUT /api/v1/organizations/{id}/wazuh-credentials`?
   Pick one pattern for consistency.
3. **GUI for purely-runtime state** (active sessions, current model
   load, etc.) — out of scope for this ADR (those are observability
   not configuration), but worth flagging that the discipline cleanly
   separates the two.

---

## Status, sign-off, next steps

This ADR is **PROPOSED**, not ACCEPTED. Before ACCEPTED:

1. Operator review of Rules 1 + 2
2. Resolution of the 3 open decisions above
3. Acknowledgment of the refactor scope (existing CLI tools)

Once ACCEPTED:

- ADR 0019 becomes the contract for all future configurable-feature work
- Existing CLI tools are tracked for migration but not blocking
- Phase 6.5 (per ADR 0018) gets implemented under this discipline from
  the start
- The catalog table in §"Rule 1" becomes a tracked checklist; each
  row gets closed as its GUI surface ships

No code ships from this commit. Design only.
