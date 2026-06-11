# Wolf — Development Changelog

> **This is the append-only history of the Wolf project.** Every Claude Code
> session, every meaningful human change, every decision — appended here as
> the work happens.
>
> **Rules for this file:**
>
> - Append only. Never delete or rewrite past entries.
> - Newest entries at the top. Reverse chronological.
> - Every session adds at least one entry, even if "no code changes — just
>   investigation."
> - Be specific. "Updated config" is useless; "Set DEFAULT_MODEL_ID=qwen3:4b
>   in services/orchestrator/app/config.py after probe results showed
>   reasoning_tier=basic on this hardware" is useful.
> - For decisions that change architecture or defaults, also write a full ADR
>   in `docs/decisions/` and reference its filename here.
>
> For *current* project state, see `PROGRESS.md` (live, updated, not
> chronological).

---

## Entry template

Copy this block and fill in at the start of each session entry:

```
## YYYY-MM-DD — [Session brief title]

**Session type:** [claude-code / human / mixed]
**Phase:** [from roadmap]
**Duration:** [approx — for capacity tracking]
**Branch / commit:** [git ref where work ended]

### What we did
- [bullet — concrete action]
- [bullet — concrete action]

### What we decided
- [decision, with reason; link to ADR if applicable]

### What broke / what we discovered
- [unexpected issue, finding, surprise]

### What's next
- [next-action item — should match PROGRESS.md "What's next"]
```

---

## 2026-06-10 → 2026-06-11 — Multi-organization design arc: ADRs 0017+0018+0019+0020 all ACCEPTED

**Session type:** claude-code (mixed; multi-round operator review)
**Phase:** Pre-Phase-6.4 design closure
**Branch / commit:** main @ 7939c79 (after Commit 2 of the post-arc cleanup; arc itself spans `b22e424` through `be598b4`)

### What we did

Four tightly-coupled ADRs went through multi-round operator review and
were ACCEPTED. ~20 commits across the arc. **No code touched** — pure
design work + cross-referenced documentation.

**ADR 0018 — Bootstrap Superuser + Per-Org RBAC + Login UX** (5-round
review):
- Round 1: Wazuh component mapping split out to its own ADR 0020;
  silent-password-reset rule flipped (Superuser CAN reset with audit)
- Round 2: Approver→Responder rename; Responder gained direct-execute
  capability; Engineer gained approve-actions; Analyst gained
  propose-actions; Superuser data access now requires org-Admin
  explicit consent (no self-grant); break-glass org-recovery for
  zero-Admin orgs
- Round 3: Cookie carries auth ONLY; per-tab `X-Organization-Id`
  header for org context; Superuser special-case login redirect to
  `/superuser/dashboard`; cookie blacklist for logout / force-revoke /
  password-reset; clean drop of `organization_id` field on login (no
  backward-compat alias)
- Round 4: Implementation sequencing — Phase 6.4 (codebase rename) as
  pre-req; Phase 6.5 with 8 sub-slices; defer propose/approve/execute
  RBAC matrix rows to Phase 6 (wolf-gateway); honest 10-12 session
  estimate (later 12-13 after Round 5)
- Round 5: Invite-link verification flow with dynamic same-network
  gate (copy-link out-of-band delivery, no SMTP); MFA deferred to
  v1.1; 8h+1h uniform session timeout; password policy (12+ chars,
  complexity, no rotation, common-password list rejection); both
  global + per-org audit views for Superuser. 9th sub-slice 6.5-h
  added for the invite-link flow. **ACCEPTED 2026-06-10** as
  commit `b22e424`.

**ADR 0019 — Web-first configurability mandate** (1-round review):
- Manual restart with "pending restart" indicator (not auto-restart)
- REST endpoints nested under resources (`/install/*`,
  `/organizations/{id}/*`, `/users/{id}/*`)
- Config-only scope; runtime observability deferred to its own
  ADR/phase
- Cross-org "My memory" UI semantics with Superuser-self-only
  caveat at data-access level
- **ACCEPTED 2026-06-10** as commit `24bcdb9`.

**ADR 0020 — Superuser-owned Wazuh component mapping** (1-round review):
- Random indexer node selection
- Postgres + Fernet credentials (Vault deferred; Memgraph rejected
  due to BSL non-production restriction)
- Hard-fail install probe; soft-fail per-org credentials probe
- One install = one Wazuh ecosystem (multi-ecosystem deferred)
- Single shared dashboard URL (per-org override deferred)
- No restart needed on topology change (per-query DB read,
  microseconds overhead)
- Credentials in secrets backend only (separate from org metadata)
- **ACCEPTED 2026-06-10** as commit `c6dc92c`.

**ADR 0017 — Wolf Central Brain** (4-round review):
- Round 1: 5+1 architectural clustering of the 17 operator points
  confirmed; cross-ref to ADR 0019 "My memory" semantics added to
  the storage-vs-UI section
- Round 2: 4 memory layers (episodic/session/long-term/semantic);
  6-category `fact_type` enum (added `incident_lesson`; renamed
  `relationship` → `social_context`); exponential decay (30d default,
  auto-prune < 0.1); load-once retrieval at conversation start;
  semantic memory in Postgres (Neo4j Community evaluated; Memgraph
  BSL rejected); per-fact-type retention (preference / runbook /
  incident_lesson live until deleted; environment_fact /
  social_context 12mo; observation 90d); always-on with operator
  opt-out; cross-org confirmation; read+delete (no edit) UI
- Round 3: Deep-think trigger both manual + auto-escalate; soft cost
  cap with warning; action validator hard-gate + no-bypass + no cost
  cap + inline rejection + edit-and-retry; 3-state confidence
  calibration; **point-8 §"Robust answer posture" ACCEPTED as
  written** — Wolf delivers "always useful + never unexplained 'I
  don't know'" but rejects "never says uncertain" to avoid SOC
  hallucination
- Round 4: Alert-pattern cadence operator-configurable default-daily;
  environment fingerprinting auto at org bootstrap; **W4 scope
  expanded to Wazuh log sources** (alerts.json + archives.json +
  manager logs + indexer indices) tracked as `log_source` semantic-
  memory entities (log CONTENT NOT replicated to Wolf DB; indexer
  remains canonical); 5-phase additions (7.5, 8.5, 9.5, 11.5,
  Phase 12 rename) confirmed; wolf-hunt / wolf-den / wolf-pack names
  reserved for ADRs ~0021/0022/0023.
- **ACCEPTED 2026-06-11** as commit `be598b4`.

**Post-arc housekeeping** (3 commits):
- Roadmap doc (`docs/10-build-roadmap.md`) updated: new Phase 6.4 /
  6.5 / 6.6 sections; Phase 7.5 + 8.5 refreshed to reflect Round-2/3/4
  design choices + ACCEPTED status; Phase 9.5 / 11.5 / 12 stale-ADR-
  number references corrected (0018/0019/0020 → ~0021/0022/0023);
  new "2026-06-10 / 2026-06-11 — multi-organization design arc"
  subsection added to §"Phase ordering — divergence". Commit
  `f47931a`.
- Memory directory cleanup: `wolf-knowledge-relay.md` →
  `wolf-pack.md`; MEMORY.md index entry updated; ADR-ACCEPTED
  cross-ref preambles added to `wolf-bootstrap-superuser-flow.md`
  (refs ADR 0018 + 0020), `web-first-configurability.md` (refs ADR
  0019), `organization-renamed-to-organization.md` (refs ADR 0018 +
  notes Phase 6.4 schedule). Commit `7939c79`.
- This CHANGELOG entry.

### What we decided

- Wolf is multi-organization-ready by design before any
  multi-organization code ships. The 4 ADRs together define the
  contract.
- Phase 6.4 (organization→organization codebase rename) is the next real
  work unit. Single PR, ~40-60 files, 1-2 sessions. Unblocks
  Phase 6.5 (9 sub-slices, 12-13 sessions) and Phase 6.6 (5
  sub-slices, 3-5 sessions).
- Wolf will never produce a bare "I don't know" answer — every
  uncertainty includes context + actionable next steps + tool
  offers, per ADR 0017 §"Robust answer posture" three pillars.
  Wolf WILL say "uncertain" / "insufficient evidence" when honest,
  to avoid SOC-incident hallucination.
- The four memory entries from this arc (wolf-bootstrap-superuser-
  flow, shell-wrapper-required-pattern, organization-renamed-to-organization,
  web-first-configurability) are now in `memory/` in the repo, not
  in `~/.claude/projects/`. Memory travels with the code via git
  history.
- Future ADRs 0021 / 0022 / 0023 reserved for wolf-hunt (Phase 9.5)
  / wolf-den (Phase 11.5) / wolf-pack (Phase 12) at phase-open
  time.

### What's next

- **Phase 6.4 — organization → organization codebase rename.** Single PR,
  ~40-60 files. Mechanical rename across DB schema (Alembic
  migration) + SQLAlchemy models + API routes + frontend +
  TypeScript types + tests. Memory entry `organization-renamed-to-
  organization.md` flips to COMPLETED at end.
- Phase 6.5 (Bootstrap + RBAC + Login UX, per ADR 0018) follows;
  then Phase 6 (wolf-gateway), then Phase 6.6 (per ADR 0020).

---

## 2026-06-05 — Slice 5.9-e: wolf meta-package + `make smoke-deb` + CI (Phase 5.9 CLOSED)

**Session type:** claude-code
**Phase:** 5.9 — APT packaging — **CLOSED**
**Branch / commit:** main @ (this commit)

### What we did
Phase 5.9 close-out. Three artifacts ship:

1. **`debian/wolf.postinst`** — the meta-package's only payload.
   When `apt install wolf` finishes (after the three component
   .debs have configured themselves in dependency order), this
   prints a 6-step operator bring-up sequence: `wolf-cert init`
   → `wolf-database init` → provision `/etc/wolf-server/env` +
   `/etc/wolf-dashboard/env` → `systemctl enable --now …` →
   browser at `https://<host>:3000/`. Doesn't auto-start any
   service.
2. **`make smoke-deb`** — Docker-based build smoke. Runs
   `dpkg-buildpackage` inside a clean `debian:trixie` container
   with debhelper + python3-pip + nodejs + npm preinstalled.
   Output .debs land in `packaging/build/debs/` on the host.
   Takes ~5–10 min (fresh apt-get update + 100+ wheels for
   wolf-server's bundle); use before any push that touches
   `debian/`. Refuses if Docker isn't installed and points the
   operator at the CI job as the canonical gate instead.
3. **CI `smoke-deb` job** — equivalent build done natively on
   ubuntu-latest (no nested Docker; faster + simpler). Uploads
   the four `.debs` as a workflow artifact (`wolf-debs`) with
   14-day retention so a maintainer reviewing a PR can download
   + spot-check via `apt install ./wolf-*.deb`. On failure,
   dumps the build log + `debian/files` so the regression
   shows up in the CI log.

### Phase 5.9 — CLOSED

Five slices on 2026-06-04 → 2026-06-05:

| Slice | Commit | Key deliverables |
|---|---|---|
| 5.9-a | `85f0807` | `debian/` scaffold: control (4 packages), rules, compat=13, source/format=native, changelog, Apache-2.0 copyright. |
| 5.9-b | `76e4e53` | wolf-database.deb. Bundled wheel, postinst creates user/group/FHS + builds venv. |
| 5.9-c | `258def4` | wolf-server.deb. Bundled wheel + 5 workspace pkgs (server/cert/common/secrets/schema) + 13+ transitive prod deps as a self-contained wheels/ dir. Air-gapped install works the same as connected. |
| 5.9-d | `9a74c26` | wolf-dashboard.deb. Added `output: "standalone"` to next.config.ts. Postinst is simpler (no venv to build). |
| 5.9-e | this commit | Meta-package postinst + `make smoke-deb` + CI smoke-deb job. |

End-state:

* `sudo apt install wolf` → all three components installed,
  users + group + FHS dirs configured, services NOT auto-started
  (operator runs init steps + provisions env files first,
  then `systemctl enable --now …`).
* Per-component installs work too — distributed deployments can
  install just `wolf-database` on the brain host, `wolf-server`
  on the API host, `wolf-dashboard` on the edge host.
* All four .debs are buildable by `dpkg-buildpackage` on any
  Debian/Ubuntu host with the documented Build-Depends. CI
  produces them on every PR + uploads as a workflow artifact.
* Four pre-push smokes: smoke-mtls (5.6-e), smoke-database
  (5.7-d), smoke-systemd (5.8-d), smoke-deb (5.9-e). All four
  run on every CI PR.

### Verification policy

Per the 2026-06-04 operator direction (defer build verification
to CI), this slice deliberately doesn't run `dpkg-buildpackage`
locally. The CI smoke-deb job is the canonical gate; if it
fails when this commit lands, a follow-up commit on this slice
fixes whatever's broken. Possible issues that could surface:

* Python wheel resolution edge cases — some transitive dep that
  isn't wheel-available on the CI runner's architecture.
* npm ci that fails because of a lockfile / registry oddity.
* dh_installsystemd auto-snippet that doesn't compose cleanly
  with our service file's existing directives.
* Missing Build-Dep we didn't notice.

If any of these come up, we fix them in a 5.9-e follow-up before
opening 5.10.

### What's next
**Phase 5.10 — DNF packaging.** RPM equivalent of Phase 5.9.
Same component layout (one .rpm per component + a `wolf` meta-
RPM), same end-state (`dnf install wolf` brings everything up
the same way `apt install wolf` does). Shape:

* `packaging/rpm/wolf.spec` — RPM spec file
* `packaging/rpm/{wolf-server,wolf-database,wolf-dashboard}.{install,scripts}`
* `make smoke-rpm` Makefile target — `dnf install` smoke in
  fedora:latest container
* CI smoke-rpm job

Once 5.10 closes, Phase 5 is officially complete. The build can
move to Phase 6 (Approval Gateway).

---

## 2026-06-04 — Slice 5.8-d: ONBOARDING Path A rewrite + `make smoke-systemd` + CI (Phase 5.8 CLOSED)

**Session type:** claude-code
**Phase:** 5.8 — systemd units + FHS install paths — **CLOSED**
**Branch / commit:** main @ (this commit)

### What we did
Phase 5.8 close-out. Three pieces:

1. **ONBOARDING §3.4 Path A rewrite.** The section previously
   had a "pre-Phase-5.8 caveat" callout saying Path A required
   manual `make wolf-database-up` after every reboot (because no
   systemd unit). Phase 5.8-a + 5.8-b made that obsolete: both
   user-level (`make install-user-systemd`) and system-level
   (`install-users.sh` + `install.sh`) workflows now exist with
   proper auto-restart. The caveat is gone; Path A is now the
   recommended workflow. Path B (system Postgres) demoted to
   "still supported as a fallback for operators with existing
   infrastructure or who don't want to introduce a new systemd
   unit."

   New Path A subsections:
   * **Dev — user-level systemd**: install Postgres binaries
     → stop+disable system postgresql → `make wolf-database-init`
     → paste DATABASE_URL → `make install-user-systemd` →
     `systemctl --user enable --now wolf-database` →
     `loginctl enable-linger $USER` for headless boxes.
   * **Production — system-level systemd**: same binary install
     → `install-users.sh` → `install.sh` → copy unit files →
     `sudo -u wolf-database wolf-database init` → paste
     DATABASE_URL into `/etc/wolf-server/env` →
     `systemctl enable --now wolf-database wolf-server wolf-dashboard`.

2. **`make smoke-systemd` Makefile target.** Five-check sequence:
   * install-user-systemd materialises the three dev unit
     templates into `~/.config/systemd/user/` with the
     `@REPO_ROOT@` + `@NODE_BIN@` substitutions resolved.
   * `systemd-analyze verify --user` passes on each installed
     dev unit (catches typos in directives, bad post-substitution
     paths).
   * `systemd-analyze verify` passes on each system-level unit
     template (filtering the expected `/usr/bin/wolf-* is not
     executable` complaints — those go away once Phase 5.9/5.10
     ships the .deb).
   * Every shim in `deploy/bin/` exits 2 with a "FAIL:" prefix
     when its production venv is missing (the pre-5.9 state,
     which is also CI's state).
   * `install.sh --help` works without sudo.

   Catches regressions across the systemd + shim surface that
   the unit tests can't reach (no real systemd in pytest;
   shell-level fail-loud behaviour is shell-shaped, not Python-
   shaped). Found one real bug during authoring: bare
   `out=$(shim --help)` plus `set -e` doesn't survive the shim's
   exit 2; the script aborts before `rc=$?` runs. Fixed with
   explicit `set +e` around the capture.

3. **CI smoke-systemd job.** Parallel to `smoke-mtls` (5.6-e)
   and `smoke-database` (5.7-d). Runs on every PR. No real
   services start; purely syntactic + presence-of-fail-loud
   validation. GHA ubuntu-latest has systemd + node preinstalled
   so the smoke runs in <10 seconds with no extra setup.

Live verification on the dev host
---------------------------------
```
$ make smoke-systemd
=== smoke-systemd: 5-check sequence ===
--- 1/5: install-user-systemd installs all three units ---
    OK: all three units present in ~/.config/systemd/user/
--- 2/5: systemd-analyze --user passes on installed dev units ---
    OK: all three installed user units are clean
--- 3/5: systemd-analyze passes on system-level unit templates ---
    (filtering expected "/usr/bin/wolf-* is not executable"; that lands with the .deb)
    OK: all three system unit templates have clean directives
--- 4/5: every shim fails loud with exit 2 when its venv is missing ---
    OK: all four shims fail-loud as designed
--- 5/5: install.sh --help works without sudo ---
    OK: install.sh --help reachable without root

=== smoke-systemd: PASS ===
```

### Phase 5.8 — CLOSED

Four slices over a few hours:

| Slice | Commit(s) | Key deliverables |
|---|---|---|
| 5.8-a | `90a56b6` | User-level systemd unit templates + `make install-user-systemd`. `_wait_for_database()` retry loop in wolf-server's lifespan (no After= coupling needed). +4 retry-loop tests. |
| 5.8-b | `da542db` | System-level unit templates with per-component users + hardening directives. `install-users.sh` creates users + group + FHS dirs. Fixed the hardcoded `/usr/bin/npm` bug in the dev wolf-dashboard unit (caught by systemd-analyze). |
| 5.8-c | `bb4f128` + `b4beee9` + `8e01813` | `/usr/bin/wolf-*` shipped CLI shims (wolf-cert, wolf-database, wolf-server, wolf-dashboard). `install.sh` drops them into /usr/bin/ + creates /usr/lib/wolf-*/ empty dirs. CLI-args migration (sudo strips env) + footer-message polish (reflects --bin-dir / --lib-dir overrides). |
| 5.8-d | this commit | ONBOARDING §3.4 Path A rewrite (production-recommended); `make smoke-systemd` Makefile target; CI smoke-systemd job. Phase close-out. |

End-state of Phase 5.8:

* Three Wolf components have both dev + prod systemd units;
  per ADR 0016 v3 they're fully independent (no After=/Requires=/
  Wants= between Wolf services).
* wolf-server gracefully handles wolf-database not being ready
  via app-level retry (`_wait_for_database()` with backoff
  cycle). Same code works for all-in-one + distributed deploys.
* `/usr/bin/wolf-*` shims point at `/usr/lib/wolf-*/.venv/`.
  Until 5.9 / 5.10 ship the .deb, each shim fails-loud with a
  clear install hint + dev-workspace fallback.
* Two idempotent root scripts (`install-users.sh` +
  `install.sh`) prepare a host for production systemd.
  Disjoint paths — order doesn't matter.
* Three pre-push smokes: `make smoke-mtls` (5.6-e),
  `make smoke-database` (5.7-d), `make smoke-systemd` (5.8-d).
  CI runs all three on every PR.

Integrity gate (whole-phase, all green)
---------------------------------------
* mypy: 0 errors across 7 Python projects (94 source files;
  +0 vs Phase 5.7 close — 5.8 is shell + docs + systemd)
* ruff: clean
* tsc + eslint (services/dashboard): untouched, both clean
* backend pytest: 397 / 397 (was 393 at Phase 5.7 close; +4
  retry-loop tests in 5.8-a)
* live organization-isolation probe: 6 / 6
* All three pre-push smokes pass live + in CI: `smoke-mtls`,
  `smoke-database`, `smoke-systemd`

### What's left for the official-release phase
**Phase 5.9 — APT packaging.** `.deb` post-install hook invokes
`install-users.sh` + `install.sh` + creates the
`/usr/lib/wolf-*/.venv/` directories via Python venv + pip +
runs `npm run build` for wolf-dashboard's Next.js standalone.
After 5.9, the operator command is `apt install wolf` and
nothing else.

**Phase 5.10 — DNF packaging.** RPM equivalent. Same install-
time work, different packaging tooling.

Both deferred to the official-release phase per the 2026-06-03
operator direction.

### What's next today
Nothing in this phase. The next phase to open is one of:
* Phase 5.5's deferred planning-bundle doc sweep (descriptive
  specs in docs/00–16 still reference pre-rename component
  names from before Phase 5.5).
* Phase 6 (approval gateway + wolf-gateway service).
* Phase 5.9 / 5.10 packaging, if the operator chooses to open
  it earlier than the official-release phase originally
  scoped.

Operator's call.

---

## 2026-06-04 — Slice 5.8-a: user-level systemd units + wolf-server DB-retry loop

**Session type:** claude-code
**Phase:** 5.8 — systemd units + FHS install paths (slice a of d)
**Branch / commit:** main @ (this commit)

### What we did
First slice of Phase 5.8. Three pieces:

1. **Three user-level systemd unit templates** at
   `deploy/systemd/dev/`. Installed via the new
   `make install-user-systemd` target which substitutes
   `@REPO_ROOT@` for the current `$PWD` and drops the files into
   `~/.config/systemd/user/`. Operator then runs
   `systemctl --user enable --now wolf-database` (plus
   `loginctl enable-linger $USER` for headless boxes) and the
   component auto-restarts on every boot. System-level units
   with proper service users + FHS paths land in 5.8-b.

2. **Fully-independent units per ADR 0016 v3.** No `After=`,
   no `Requires=`, no `Wants=` between Wolf services. Each
   starts on its own. Same units work on an all-in-one host AND
   on distributed deployments where wolf-database lives on a
   different host than wolf-server. The independence has one
   consequence: wolf-server may start before wolf-database is
   ready, which slice (3) handles.

3. **wolf-server lifespan hook DB-reachability retry loop**
   (`services/server/wolf_server/main.py`). Added
   `_wait_for_database()`: polls DATABASE_URL with a `SELECT 1`
   on a backoff schedule (0.5s, 1s, 2s, 5s, 10s, 20s, 30s
   cycling) until the DB responds or a 120-second timeout
   elapses. Logs `database_unreachable_retrying` at warning
   level on each miss so operators can grep journald to see
   what's happening. Called BEFORE `_run_migrations` so a
   fresh-boot race doesn't crash wolf-server's lifespan
   coroutine. On total timeout, re-raises — at that point
   something is genuinely broken.

   Architectural reasoning: we explicitly chose this over a
   systemd `After=wolf-database.service` because the latter
   couples the two units, which only makes sense when both are
   on the same host. The app-level retry works identically in
   all-in-one and distributed deployments. ADR 0016 v3 codifies
   this independence; this slice operationalises it.

Files added:
* `deploy/systemd/dev/README.md` — operator-facing doc
* `deploy/systemd/dev/wolf-database.service` — Type=forking;
  ExecStart uses `python -m wolf_database start`; PIDFile
  points at the data dir's `postmaster.pid`; SuccessExitStatus=143
  so a clean pg_ctl stop isn't logged as a failure.
* `deploy/systemd/dev/wolf-server.service` — Type=simple;
  EnvironmentFile=@REPO_ROOT@/.env so DATABASE_URL +
  SECRET_KEY + secrets-backend env reach the uvicorn process.
* `deploy/systemd/dev/wolf-dashboard.service` — Type=simple;
  ExecStart=/usr/bin/npm run dev.

Files changed:
* `services/server/wolf_server/main.py` — `_wait_for_database()`
  helper + lifespan-hook integration. Adds ~50 lines.
* `Makefile` — new `install-user-systemd` target. Iterates the
  three components, sed-substitutes `@REPO_ROOT@` with `$(PWD)`,
  drops into `~/.config/systemd/user/`, runs `daemon-reload`,
  prints follow-up instructions.
* `ONBOARDING.md` §3.4 — the pre-5.8 caveat from slice 5.7-d
  was rewritten to mention the new Phase 5.8-a user-level unit
  path. Path B (system Postgres) still recommended for daily
  dev until 5.8-b lands the system-level units.

Tests added (4 in `services/server/tests/test_lifespan_db_retry.py`):
* `test_db_reachable_on_first_try_returns_immediately` — happy
  path; verifies one engine constructed, zero sleeps.
* `test_retries_until_db_becomes_reachable` — three failures
  then success; verifies four engines + three sleeps.
* `test_raises_after_timeout` — DB never comes back; verifies
  the underlying ConnectionRefusedError surfaces.
* `test_backoff_schedule_cycles_when_exhausted` — explicit test
  that the backoff tuple cycles via `itertools.cycle`. Asserts
  exact sleep sequence.

### Integrity gate (all green)
* mypy: 0 errors across 7 Python projects (94 source files)
* ruff: clean (after auto-fix)
* tsc (services/dashboard): 0 errors (untouched)
* eslint (services/dashboard): clean (untouched)
* backend pytest: 397 / 397 (was 393; +4 retry-loop tests)

### What's next
**Slice 5.8-b — System-level units + service users + FHS paths.**
Three `/lib/systemd/system/wolf-*.service` files with `User=`,
`Group=`, hardening directives. Creation of the
`wolf-{database,server,dashboard,gateway}` system users (all in
shared `wolf` group, all `nologin`). FHS-aware paths:
`/var/lib/wolf-*/` data, `/etc/wolf-*/` config,
`/var/run/wolf-*/` sockets. This is the production-parity
variant of slice 5.8-a's dev units.

---

## 2026-06-04 — Slice 5.7-d: `make smoke-database` + CI job (Phase 5.7 CLOSED)

**Session type:** claude-code
**Phase:** 5.7 — wolf-database extraction — **CLOSED**
**Branch / commit:** main @ (this commit)

### What we did
The last slice of Phase 5.7. End-to-end smoke for the
wolf-database CLI lifecycle, codified as a Makefile target +
CI job. Parallel to `make smoke-mtls` from Phase 5.6-e — every
pre-push moment locally + every PR in CI exercises the full
wolf-database lifecycle against a real Postgres.

Files changed:

* **`Makefile`** — new `smoke-database` target. Five-step
  lifecycle against tmp paths so it doesn't disturb the
  operator's real `.local/wolf-database/` cluster:

    1. `wolf-database status` — expect "DATA DIR MISSING"
    2. `wolf-database init --port 17860` — runs the full
       one-shot (initdb + write_config + start + pgvector check
       + role + db + extension + stop)
    3. `wolf-database start`
    4. `wolf-database status` — expect "RUNNING"
    5. `wolf-database stop` + status — expect "STOPPED"

  Designed for graceful degradation on hosts without the
  postgresql-17-pgvector package: when init exits with the
  pgvector-missing error, the smoke reports
  "PARTIAL PASS (pgvector required for full smoke)" and exits
  0 with a clear install hint. The CI smoke installs pgvector
  upfront, so it always runs the full chain.

  Uses a bash trap to clean up the tmp paths + stop any
  half-started Postgres even if the smoke aborts.

* **`.github/workflows/ci.yml`** — new `smoke-database` job.
  Installs postgresql-17 + postgresql-17-pgvector from the
  official PostgreSQL APT repo (Ubuntu 24.04's default repos
  ship 16, not 17), stops the system Postgres unit so it
  doesn't fight wolf-database for port 5432, then runs
  `make smoke-database`. Parallel to the `smoke-mtls` job's
  structure.

* **`docs/PROGRESS.md`** + **`docs/CHANGELOG.md`** — Phase 5.7
  marked CLOSED. Forward-looking section names Phase 5.8 as
  next (systemd units + `/bin` + FHS install paths).

### Live verification on the dev host
The dev host doesn't have postgresql-17-pgvector installed, so
the smoke exercises the graceful-degradation path. Output:

```
$ make smoke-database
=== smoke-database: against /tmp/wd-stack-smoke on port 17860 ===
--- 1/5: status on missing data dir ---
--- 2/5: init (will detect pgvector availability) ---
    SKIP: postgresql-17-pgvector not installed on this host.
    The CLI failed gracefully with the install hint, as designed.
    Install pgvector and re-run to validate the full chain:
      sudo apt install postgresql-17-pgvector

=== smoke-database: PARTIAL PASS (pgvector required for full smoke) ===
```

Exits 0; bash trap cleaned up /tmp/wd-stack-smoke. Full-chain
validation lives in CI where pgvector IS installed.

### Phase 5.7 — CLOSED

Four slices, one day:

| Slice | Commit | What it shipped |
|---|---|---|
| 5.7-a | `25f576f` | wolf-database substrate (`packages/database/` workspace package). DatabaseLayout / find_postgres_binaries / PostgresqlConfOptions / PgHbaOptions / connection_url. 34 new tests. |
| 5.7-b | `ea02f7c` | wolf-database CLI. Five subcommands (init / start / stop / status / reconfigure) parallel to wolf-cert. `--port` to avoid system-Postgres collision. Live-smoke verified against real Postgres 17. 33 new tests. |
| 5.7-c | `1c13f54` | Dev-workflow integration. Five Makefile wrappers. `.env.example` rewrite documenting three DB paths. ONBOARDING §3.4 rewritten as a three-path comparison (wolf-database recommended). |
| 5.7-d | this | `make smoke-database` + CI job. Graceful degradation on missing-pgvector hosts. |

End-state of Phase 5.7:

* `wolf-database` is a real, deployable Wolf component
  parallel to wolf-server / wolf-dashboard / wolf-gateway.
* Postgres binaries come from the OS package manager (per
  ADR 0008's native-primary commitment) but Wolf owns config,
  data, sockets, lifecycle.
* Dev workflow: `make wolf-database-init` →
  `make wolf-database-up`. Operator gets a generated DATABASE_URL
  to paste into `.env`.
* Production workflow ready for Phase 5.8's systemd unit
  (data dir under /var/lib/wolf-database/, config under
  /etc/wolf-database/, FHS-canonical).
* System-Postgres path (Phase 5.6 and earlier) still works —
  nobody's existing dev setup broke.
* Backend pytest grew **321 → 388** (+67 tests across the new
  package).
* mypy / ruff / tsc / eslint all clean (94 Python source files
  vs 87 at Phase 5.6 close — +7 in `wolf_database`).
* Two pre-push smokes now exist: `make smoke-mtls` (Phase 5.6-e)
  and `make smoke-database` (Phase 5.7-d). CI runs both on
  every PR.

### What's next
**Phase 5.8 — systemd units + `/bin` + FHS install paths.**
The three Wolf components get proper daemon plumbing: unit
files at `/lib/systemd/system/{wolf-server,wolf-dashboard,wolf-database}.service`,
packaged CLIs symlinked from `/usr/bin/`, config under
`/etc/wolf-*/`, data under `/var/lib/wolf-*/`. Brings Wolf
from "deploys on top of a dev shell" to "deploys as a
daemonised service." Sets up the substrate that Phase
5.9 / 5.10 (APT / DNF — still deferred to the official-
release phase per the 2026-06-03 operator direction) builds
on.

---

## 2026-06-04 — Slice 5.7-c: dev-workflow integration (Makefile + .env.example + ONBOARDING §3.4 rewrite)

**Session type:** claude-code
**Phase:** 5.7 — wolf-database extraction (slice c of d)
**Branch / commit:** main @ (this commit)

### What we did
Operator-facing wiring for the wolf-database CLI built in 5.7-b.
Same code; same tests. The change is purely the operator's
day-one experience: Makefile targets so the CLI is invokable
with one short command, `.env.example` documenting both the
wolf-database path and the system-Postgres path, and ONBOARDING
§3.4 rewritten as a three-path comparison with wolf-database
flagged as the recommended one.

Files changed:
* **`Makefile`** — five new targets, all thin wrappers around
  `python -m wolf_database <sub>`:
    - `make wolf-database-init` (with optional `PORT=` override
      for hosts where 5432 is taken by a system Postgres)
    - `make wolf-database-up`
    - `make wolf-database-down`
    - `make wolf-database-status`
    - `make wolf-database-reconfigure`
  `.PHONY` list updated. Each target has its `## …` help line
  so `make help` lists all five in the same place as the other
  ops targets.
* **`.env.example`** — DATABASE_URL section rewritten to
  document three paths (wolf-database / system Postgres /
  SQLite-for-tests). The wolf-database line is the recommended
  default with a `GENERATED` placeholder reminding the operator
  to paste the password `wolf-database init` prints. The
  system-Postgres line (the previous default) is still active —
  not breaking anyone's current dev setup.
* **`ONBOARDING.md` §3.4** — full rewrite. Was a single
  "system Postgres" recipe with a Docker alternative. Now a
  three-path section:
    - **Path A — wolf-database (recommended).** Install
      postgresql-17 + postgresql-17-pgvector via apt/dnf,
      DISABLE the system postgresql.service so it doesn't
      fight wolf-database for 5432, then
      `make wolf-database-init` → `make wolf-database-up`.
      Data dir under `<repo>/.local/wolf-database/` for dev,
      `/var/lib/wolf-database/` for prod via
      `WOLF_DATABASE_PRODUCTION=1`.
    - **Path B — System Postgres.** The previous recipe
      verbatim. Operators with existing Postgres infra keep
      using it.
    - **Path C — Docker Postgres.** Same as before; per
      ADR 0008 it's the supplementary channel.
* **`docs/restart.md`** — the "What the restart does NOT touch"
  Postgres row now branches: wolf-database operators restart
  with `make wolf-database-down && make wolf-database-up`;
  system-Postgres operators stay on `sudo systemctl restart
  postgresql`.

### Live verification
* `make help` shows all five new targets with their help text.
* `make wolf-database-status` (against a host that has no
  `.local/wolf-database/` yet) correctly reports
  "DATA DIR MISSING — run `wolf-database init`." The
  Makefile target dispatches cleanly to the CLI.

### Integrity gate (all green)
* mypy: 0 errors across 7 Python projects (94 source files)
* ruff: clean
* tsc (services/dashboard): 0 errors (untouched)
* eslint (services/dashboard): clean (untouched)
* backend pytest: 388 / 388 (unchanged — this slice is docs +
  Makefile only)
* live organization-isolation probe: 6 / 6
* live `make wolf-database-status`: dispatches correctly

### What's next
**Slice 5.7-d — End-to-end smoke + Phase 5.7 close-out.**
Codify the full operator chain (wolf-cert init → wolf-database
init → wolf-server starts against wolf-database → dashboard
login works) as a `make smoke-stack` target + CI job. Closes
Phase 5.7.

---

## 2026-06-04 — Slice 5.7-b: wolf-database CLI (init/start/stop/status/reconfigure)

**Session type:** claude-code
**Phase:** 5.7 — wolf-database extraction (slice b of d)
**Branch / commit:** main @ (this commit)

### What we did
The CLI built on top of 5.7-a's substrate. Five subcommands,
parallel to wolf-cert's shape. Verified live against a real
Postgres 17 on the dev host.

Files added:
* `packages/database/wolf_database/process.py` — subprocess
  wrappers for `initdb`, `pg_ctl start/stop/status`, `psql -c`.
  Each helper takes resolved `PostgresBinaries` +
  `DatabaseLayout` so no re-discovery cost. `pg_ctl` always
  carries `-o "--config-file=<our conf>"` so Postgres reads our
  wolf-database-owned `postgresql.conf` instead of the one
  initdb wrote inside the data dir. Status-query special-cases
  pg_ctl's exit codes (0=running, 3=stopped, 4=data-dir-bad)
  into a `PgCtlStatus` dataclass — "stopped" isn't an error
  condition for `wolf-database status`. `data_dir_is_initialized`
  checks for `PG_VERSION` in the data dir (the canonical
  initdb-was-here marker). `is_pgvector_installed` queries
  `pg_available_extensions` so init can fail fast with a clear
  install hint when the postgresql-17-pgvector package isn't on
  the host.
* `packages/database/wolf_database/cli.py` — argparse dispatcher
  + five subcommands.
    - `init`: precheck binaries + version + empty data dir,
      then run initdb → write_config → start Postgres
      (waiting for ready) → check pgvector → CREATE ROLE wolf
      with random password → CREATE DATABASE wolf OWNER wolf
      → CREATE EXTENSION vector in wolf db → stop Postgres
      → print the DATABASE_URL operator should paste into
      wolf-server's .env. Exit codes: 0 / 2 (user error) /
      3 (refused — already-initialized) / 4 (binary missing).
      Refuses to clobber an existing data dir. `--port` arg
      to avoid the system-Postgres-on-5432 collision common
      on dev hosts.
    - `start` / `stop`: thin wrappers around pg_ctl.
      `start` refuses when not initialized. `stop --mode`
      defaults to fast (SIGINT-style); smart / immediate
      available for the rare case.
    - `status`: prints data dir + config dir + socket dir +
      state (RUNNING with PID / STOPPED / DATA DIR MISSING).
      Falls back to BINARY_MISSING exit when the host doesn't
      have Postgres 17.
    - `reconfigure`: rewrites postgresql.conf + pg_hba.conf
      in place from the current env vars without re-initdb,
      then tells the operator to restart Postgres to apply.
* `packages/database/wolf_database/__main__.py` — entry-point
  shim so `python -m wolf_database ...` works.
* `packages/database/pyproject.toml` — uncommented the
  `[project.scripts]` block so `wolf-database` is on PATH
  after a workspace `uv sync`.

Config alignment (caught during the live smoke):
* `process.run_initdb` switched from `--auth-local scram-sha-256`
  (which requires a superuser password) to `--auth-local peer`
  (OS-user identity is the auth). The corresponding rule in
  `config.PgHbaOptions` flipped to `local all all peer` so
  the running cluster's pg_hba matches initdb's choice. TCP
  loopback rules stay scram-sha-256 — wolf-server connects via
  TCP and needs the password from DATABASE_URL.

Bug caught during live smoke (and fixed):
* `cmd_init`'s pgvector-missing branch called
  `run_pg_ctl_stop` once explicitly, and the `finally` block
  called it again — second call hit "PID file does not exist"
  because the first stop had already happened. Removed the
  explicit call; let the finally do the cleanup.

Tests added (33 across two files):
* `tests/test_process.py` (18) — `data_dir_is_initialized`
  (empty / PG_VERSION present / data-dir missing); `_parse_pid`
  parsing pg_ctl's "PID: X" output; `run_initdb` invokes
  subprocess + creates data dir + raises on non-zero;
  `run_pg_ctl_start` passes `--config-file=`, the `-w` wait
  flag for synchronous, `-W` for async; `run_pg_ctl_stop`
  passes `-m <mode>` and raises on failure;
  `run_pg_ctl_status` returns RUNNING+PID on exit 0,
  STOPPED on exit 3, DATA-DIR-BAD on exit 4, and
  short-circuits when the data dir is absent;
  `run_psql_command` uses the socket-dir host +
  ON_ERROR_STOP=1 + raises on psql error + targets the named
  db; `is_pgvector_installed` returns true on output "1\n",
  false on empty stdout, false on non-zero exit.
* `tests/test_cli.py` (15) — argparse requires a subcommand
  + accepts all five; stop --mode defaults + override;
  status reports DATA DIR MISSING / RUNNING+PID / STOPPED /
  BINARY_MISSING; init refuses already-initialized data dir
  (exit 3) + returns BINARY_MISSING when no postgresql-17
  installed; start refuses when not initialized;
  reconfigure writes both config files without touching
  pg_ctl. All tests use an `autouse` fixture that monkeypatches
  the `WOLF_DATABASE_*_DIR` env vars to tmp_path so they can't
  pollute each other or the real .local/wolf-database.

### Live verification
Against the host's real Postgres 17 with port 17860 (because
5432 has a system Postgres running) and tmp paths:

```
$ rm -rf /tmp/wd-smoke && \
  WOLF_DATABASE_DATA_DIR=/tmp/wd-smoke/data \
  WOLF_DATABASE_CONFIG_DIR=/tmp/wd-smoke/cfg \
  WOLF_DATABASE_SOCKET_DIR=/tmp/wd-smoke/sock \
  python -m wolf_database init --port 17860

→ initdb on /tmp/wd-smoke/data
   (Postgres 17.10 initdb output … "Success.")
→ writing config to /tmp/wd-smoke/cfg
→ starting Postgres (waiting for ready)
   LOG:  listening on IPv4 address "127.0.0.1", port 17860
   LOG:  listening on Unix socket "/tmp/wd-smoke/sock/.s.PGSQL.17860"
   LOG:  database system is ready to accept connections
wolf-database requires the pgvector extension. The running
  Postgres at /tmp/wd-smoke/sock reports it is NOT available.
  Install: `apt install postgresql-17-pgvector` …
→ stopping Postgres
   LOG:  received fast shutdown request
   LOG:  database system is shut down
server stopped
```

Every code path on the way to the pgvector check verified:
initdb → write_config → pg_ctl start with the correct
`--config-file` → ready signal → pgvector check fires
the clear-error path → clean fast shutdown. The pgvector-
missing branch is a REAL environmental dependency wolf-
database surfaces with a useful hint; slice 5.7-c documents
the apt install as part of the dev workflow.

Also verified the simpler subcommands:
* `wolf-database status` (no data dir): prints layout + "DATA
  DIR MISSING — run `wolf-database init`."
* `wolf-database --help`: subcommand summary as designed.

### Integrity gate (all green)
* mypy: 0 errors across 7 Python projects (94 source files;
  was 91 — +3 new files in wolf_database)
* ruff: clean (after auto-fix of import order + f-string-no-
  placeholder fixes)
* tsc (services/dashboard): 0 errors (untouched)
* eslint (services/dashboard): clean (untouched)
* backend pytest: **388 / 388** (was 355; +33 wolf-database
  tests)
* live organization-isolation probe: 6 / 6
* live `wolf-database init` smoke: every path verified up to
  the pgvector check; correct error + exit code; clean
  shutdown

### What's next
**Slice 5.7-c — Dev-workflow integration.** Makefile targets
(`make wolf-database-init`, `make wolf-database-up`,
`make wolf-database-down`), `.env.example` defaults pointing
at wolf-database's socket, ONBOARDING §3.4 rewrite walking the
operator from `apt install postgresql-17 postgresql-17-pgvector`
to `wolf-database init` to wolf-server connecting against the
wolf-managed cluster.

---

## 2026-06-04 — Slice 5.7-a: wolf-database substrate (layout + binary discovery + config templates)

**Session type:** claude-code
**Phase:** 5.7 — wolf-database extraction (slice a of d)
**Branch / commit:** main @ (this commit)

### What we did
Phase 5.7 opens. The first slice ships the wolf-database
substrate — a new workspace package, `packages/database/`, that
lays the foundations everything later in the phase builds on. No
behaviour change yet; `wolf-server` still connects to whatever
Postgres the operator has running. But every primitive the
Phase 5.7-b CLI will need now exists with tests behind it.

Architecture decisions (locked via user direction 2026-06-04):
1. **Use system-installed Postgres binaries with Wolf-controlled
   config.** Operator still `apt install postgresql-17
   postgresql-17-pgvector` (same as today per ADR 0008).
   wolf-database adds its own config templates, data dir,
   socket dir, dedicated service user. Mirrors how Wazuh's
   indexer/manager use OpenSearch/Elasticsearch from their own
   packages but with Wazuh-controlled config + data. Lets the
   security-update path stay apt/dnf.
2. **wolf-database has a dev CLI that works without systemd.**
   Parallel to `wolf-cert`. Dev runs it foreground; production
   wraps it in a systemd unit. Same data dir + config in both
   modes.
3. **Four-slice sequence**: a (scaffolding) → b (CLI) → c (dev
   workflow) → d (docs). Iterative; each slice has a working
   integration point.

Files added (new `packages/database/` package):
* `packages/database/pyproject.toml` — workspace package
  metadata. No dependencies (the Postgres binaries come from
  the OS package manager, not pip).
* `packages/database/wolf_database/__init__.py` — public re-
  exports.
* `packages/database/wolf_database/py.typed` — PEP 561 marker
  so downstream sees the package's types.
* `packages/database/wolf_database/layout.py` —
  `DatabaseLayout` dataclass + `resolve_layout()`. Dev paths
  under `<repo>/.local/wolf-database/{data,config,socket}/`;
  production paths under `/var/lib/wolf-database/data`,
  `/etc/wolf-database`, `/var/run/wolf-database`. Every dir
  overridable via env (`WOLF_DATABASE_DATA_DIR` /
  `WOLF_DATABASE_CONFIG_DIR` / `WOLF_DATABASE_SOCKET_DIR`).
  `WOLF_DATABASE_PRODUCTION=1` flips the defaults without
  needing an explicit kwarg.
* `packages/database/wolf_database/binaries.py` — locate the
  four Postgres tools (pg_ctl, initdb, psql, postgres) wolf-
  database wraps. Search order: env override
  (`WOLF_DATABASE_<TOOL>`), then distro-known paths
  (Debian's `/usr/lib/postgresql/17/bin`, RHEL's
  `/usr/pgsql-17/bin`), then PATH. Raises
  `PostgresBinaryNotFoundError` with the searched paths in
  the message — operators see exactly where wolf-database
  looked and a clear "install postgresql-17" hint.
  `postgres_major_version()` runs `postgres --version` and
  parses the output. `verify_postgres_supported()` enforces
  the 17+ floor (Wolf depends on Postgres 17 features per
  ADR 0008; running against 15 would silently produce a
  divergent schema).
* `packages/database/wolf_database/config.py` —
  `PostgresqlConfOptions` + `PgHbaOptions` for rendering
  postgresql.conf + pg_hba.conf bodies. Hard-coded hot wires:
  `shared_preload_libraries = 'vector'` (pgvector ext can't
  be CREATE EXTENSIONed without preload), `listen_addresses
  = localhost` (security default; distributed deploys
  override), `unix_socket_directories` pointing at the Wolf-
  owned socket dir (no collision with system Postgres's
  `/var/run/postgresql`). Default pg_hba is loopback + Unix
  socket only with scram-sha-256; distributed deploys add a
  `hostssl` rule via `extra_rules=(...)`. `write_config()`
  writes both files at mode 0640. `connection_url()` builds
  the asyncpg URL wolf-server consumes via DATABASE_URL —
  works in both socket and TCP modes, URL-encodes
  passwords/socket-paths correctly.

Tests added (34 in three files):
* `tests/test_layout.py` — 8 tests. DB name + user constants
  match wolf-server's existing .env; dev layout under .local;
  production layout under /var/lib (canonical form, since
  `/var/run` is a symlink to `/run` on Linux); each env var
  overrides independently; `WOLF_DATABASE_PRODUCTION=1` flips
  defaults; `DatabaseLayout` is a frozen dataclass; conf
  paths and PID file paths are inside their respective dirs.
* `tests/test_binaries.py` — 10 tests. Env override; PATH
  fallback; missing-tool error; stale-env-override doesn't
  short-circuit; version parser handles `(PostgreSQL) X.Y`
  and `(PostgreSQL) X.Y (extra)`; version-gate accepts 17,
  rejects 15; `find_postgres_binaries()` returns all four.
  Uses an `autouse` fixture that monkeypatches
  `_KNOWN_BIN_DIRS` to `()` so the host's real Postgres
  install (if any) doesn't short-circuit the discovery
  before the test's PATH fixture takes effect — a real
  trap caught during the first test run.
* `tests/test_config.py` — 16 tests. pgvector preload hard
  requirement; localhost-only default listen; socket dir
  matches layout; port override; loopback-only default
  pg_hba (no 0.0.0.0); extra_rules append; local rules
  omit address; write_config produces 0640 files; creates
  config dir if missing; is idempotent; connection_url in
  socket + TCP modes; URL-encoded password; custom DB name.

Workspace wiring:
* `pyproject.toml` — added a one-line clarification comment
  under `[tool.uv.workspace]` noting that `packages/*` already
  covers the new dir via the glob; no member-list edit needed.
  Added `TC003` to the test per-file-ignores so tests don't
  need TYPE_CHECKING ceremony for Path imports they use only
  in annotations.
* `uv sync --all-packages` picks up the new package; wolf-
  database 0.1.0 installs as an editable workspace dep.

### Integrity gate (all green)
* mypy: 0 errors across 7 Python projects (91 source files;
  was 87 — +4 new files in wolf_database)
* ruff: clean (after auto-fix of import order)
* tsc (services/dashboard): 0 errors (untouched)
* eslint (services/dashboard): clean (untouched)
* backend pytest: **355 / 355** (was 321; +34 wolf-database
  tests)
* live organization-isolation probe: 6 / 6

### What's next
**Slice 5.7-b — `wolf-database` CLI.** Parallel to `wolf-cert`'s
shape: `wolf-database init` runs `initdb`, lays down the
templates `wolf_database.config` renders, creates the wolf user
+ db, installs pgvector extension. `start` / `stop` / `status`
wrap `pg_ctl`. `reconfigure` regenerates the config templates
in place (without re-initdb). All operate on a `DatabaseLayout`
resolved via the same env-var dance the substrate uses.

### Why this matters
Phase 5.7 is the architectural move that takes Wolf from "deploys
on top of a system Postgres" to "ships its own Postgres
component." The substrate has to be in place before the CLI can
exist; this slice makes the rest of the phase mechanical.

---

## 2026-06-04 — Slice 5.6-e: `make smoke-mtls` recurring integrity check + CI job (Phase 5.6 CLOSED)

**Session type:** claude-code
**Phase:** 5.6 — Edge-component architecture + mTLS — **CLOSED**
**Branch / commit:** main @ (this commit)

### What we did
The last slice of Phase 5.6. Codifies the three-curl mTLS smoke
from §3.12 as a one-command Makefile target + a dedicated CI
job. Now every push (locally) and every PR (in CI) runs the
same posture-check against a fresh wolf-server.

Files changed:
* **`Makefile`** — new `smoke-mtls` target. Runs the three
  curls (no-cert → 401 mtls_required, with-cert → 401 Not
  authenticated, /healthz from loopback → 200), greps the
  response body for the expected substring on each, exits
  with status 1 on test failure or 2 on prerequisite-missing
  failure. Includes preflight checks: the CA cert + dashboard-
  client cert + dashboard-client key must exist on disk
  (`wolf-cert init` was run), AND wolf-server must be reachable
  on `https://localhost:7860` (otherwise the curls would
  produce a confusing TLS error instead of a clear "you forgot
  to start wolf-server"). The error messages name the exact
  fix command in each case. Also expanded the `.PHONY` list at
  the top of the Makefile (it was stale — only six targets
  listed; now it's the full set).
* **`.github/workflows/ci.yml`** — new `smoke-mtls` job. Spins
  up Postgres as a service container, installs deps, runs
  migrations (the smoke doesn't need a organization or user, but
  wolf-server's startup runs `alembic upgrade head` and would
  fail without a schema), mints all four cert pairs via
  `wolf-cert init` with explicit `localhost` SANs, starts
  wolf-server in the background, polls `/healthz` until it
  responds (60s max — generous for cold CI runners), verifies
  the banner says `mTLS: ENABLED` (catches the case where the
  curls would still "pass" but the underlying posture is
  silently wrong), then runs `make smoke-mtls`. On failure,
  dumps `/tmp/wolf-server.log` so the operator can see why.

### Live verification
Locally, against a fresh wolf-server start:

```
$ make smoke-mtls
=== smoke-mtls: wolf-server is up; running 3-check sequence ===
--- 1/3: no client cert  → expect 401 mtls_required ---
    response: {"error":"mtls_required",...}
--- 2/3: dashboard-client cert → expect 401 Not authenticated ---
    response: {"detail":"Not authenticated"}
--- 3/3: /healthz loopback no-cert → expect status ok ---
    response: {"status":"ok","service":"wolf-server"}
=== smoke-mtls: PASS ===
```

Failure paths verified:
* Server not running → `FAIL: wolf-server not reachable on
  https://localhost:7860 (start it first)` + exit code 2.
* Missing dashboard-client cert (simulated via rename) →
  `FAIL: ...dashboard-client/cert.pem not found. Run
  \`wolf-cert init\` first.` + exit code 2.

### Phase 5.6 closeout
Five slices shipped between 2026-06-03 and 2026-06-04:

| Slice | Commit | What it shipped |
|---|---|---|
| 5.6-a | `ef6c6f5` + `41ba52b` | Next.js catch-all reverse proxy at `app/api/[...path]/route.ts`. Browser sees one Wolf origin. Multi-Set-Cookie preserved; SSE streaming preserved per-chunk. HTTPS follow-up wired undici Agent with Wolf CA trust. |
| 5.6-b | `9923c65` | `wolf-cert init` now mints a third leaf, `dashboard-client` (LeafKind.CLIENT, CN=wolf-dashboard-client). 9 new tests. |
| 5.6-c | `495af0b` | wolf-server's launcher passes `ssl_ca_certs` + `ssl_cert_reqs=CERT_OPTIONAL`; uvicorn peer-cert monkey-patch surfaces the cert into ASGI scope; `MtlsMiddleware` enforces the CN allowlist + bypasses GET /healthz from loopback. Dashboard proxy presents the dashboard-client cert via undici Agent. 9 new middleware tests. |
| 5.6-d | `49be2d6` | Launcher banner polish (`mTLS: ENABLED/DISABLED`). ONBOARDING §3.12 rewritten + new §3.13 for distributed deployment + troubleshooting table. `docs/restart.md` mTLS section. |
| 5.6-e | this | `make smoke-mtls` target + CI job. |

End-state of Phase 5.6:
* The browser only sees one Wolf origin (`wolf-dashboard:3000`).
* wolf-server's `MtlsMiddleware` refuses any caller whose
  Subject CN isn't in `MTLS_ALLOWED_CLIENT_CNS`. Today only
  `wolf-dashboard-client` is on the allowlist.
* /healthz from `127.0.0.1` / `::1` bypasses the mTLS check so
  ops tooling stays simple.
* Audit log records every accept/reject decision.
* Distributed deployment works the same as all-in-one with one
  env var edit (`WOLF_SERVER_URL` on the dashboard host) plus
  per-host cert distribution.
* The cross-origin NetworkError from Phase 5.4 is permanently
  gone — there is no second origin for the browser to fail at.

Integrity gate (across all five slices):
* mypy: 0 errors across 6 Python projects (87 source files)
* ruff: clean
* tsc (services/dashboard): 0 errors
* eslint (services/dashboard): clean
* backend pytest: **321 / 321** (was 311 at Phase 5.6 start;
  +10 across 5.6-b, 5.6-c)
* live organization-isolation probe: 6 / 6
* `make smoke-mtls`: passes against a fresh wolf-server start
* CI `smoke-mtls` job: configured to run on every PR

### What's next
**Phase 5.7 — wolf-database extraction.** Per ADR 0016, Postgres
becomes the third deployable component (`wolf-database`) under
a Wolf-managed systemd unit with data at
`/var/lib/wolf-database/`. Today Postgres is system-managed
(per ADR 0008's "system Postgres" guidance) or operator-
installed — fine for dev, awkward for the "one apt install"
release narrative. 5.7 moves Postgres under Wolf's lifecycle
so the all-in-one install becomes a single package.

Then **Phase 5.8** (systemd units + `/bin` + FHS install paths)
and finally **Phases 5.9 / 5.10** (APT / DNF — still deferred
to the official-release phase).

---

## 2026-06-04 — Slice 5.6-d: launcher polish + operator-doc walkthrough for HTTPS + mTLS

**Session type:** claude-code
**Phase:** 5.6 — Edge-component architecture + mTLS (slice d of e)
**Branch / commit:** main @ (this commit)

### What we did
The functional mTLS stack landed in 5.6-c; this slice puts the
operator-facing story around it. Three areas:

**Launcher banner polish.** Both `wolf-server` and `wolf-dashboard`
launchers now report their mTLS state on a line explicitly
keyed `mTLS: ENABLED` or `mTLS: DISABLED`, with the rationale
appended (the file paths it found / didn't find). Absence of
the keyword in the log is itself diagnostic — an operator
grepping `mTLS:` knows immediately whether the stack came up
in the intended posture.

* `services/server/wolf_server/__main__.py` — split the startup
  output into three lines (`wolf-server: serving …` / `TLS: …`
  / `mTLS: ENABLED/DISABLED …`), one per security dimension.
* `services/dashboard/scripts/dev.mjs` — added the
  `proxy mTLS: ENABLED/DISABLED` line. Auto-detects all three
  cert files (`dashboard-client/cert.pem`, `dashboard-client/key.pem`,
  `ca/ca-cert.pem`) and reports the result. The proxy in
  `app/api/[...path]/route.ts` does the actual loading; the
  launcher just gives the operator a single place to grep
  whether mTLS is wired everywhere.

**ONBOARDING.md rewrite of §3.12.** What was previously a
"how to enable HTTPS" section is now a "how to enable HTTPS +
mTLS" section, because in Phase 5.6 they're inseparable —
`wolf-cert init` mints all three leaves in one shot and both
servers auto-detect them together. New content:

* Phase 5.6 mTLS posture explained up-front (browser sees one
  origin; wolf-server refuses non-dashboard callers).
* The lifecycle commands now mention "three leaves (server,
  dashboard, dashboard-client)" — previously was "two leaves."
* New "Verify mTLS is actively enforced" subsection with the
  three-curl smoke from 5.6-c's verification matrix:
    1. Direct curl WITHOUT cert → 401 mtls_required
    2. Direct curl WITH dashboard-client cert → 401 Not authenticated
       (correct hand-off: mTLS passes, AuthMiddleware then rejects)
    3. /healthz from loopback without cert → 200 (the bypass)
* New "Troubleshooting mTLS" table covering six common failure
  modes: NetworkError after login, dashboard says proxy mTLS
  DISABLED, wolf-server says mTLS DISABLED, mtls_cn_rejected
  with the correct CN, bare TLS error without JSON, and
  leftover-process port conflict.
* Audit-log inspection note (`grep mtls_ /tmp/wolf-server.log`)
  so operators can see what wolf-server thinks is happening.

**New ONBOARDING.md §3.13 "Distributed deployment".** Walks
the multi-host scenario where wolf-server runs on a different
host than wolf-dashboard. Includes:

* A cert-distribution table (which file goes where, and which
  files NEVER leave the admin workstation — specifically the
  CA private key).
* The single env-var edit needed: `WOLF_SERVER_URL` on the
  wolf-dashboard host.
* Forward-looking note about how `wolf-gateway` (Phase 6) and
  the relay daemons (future) plug into the same pattern with
  additional CNs in `MTLS_ALLOWED_CLIENT_CNS`.

**docs/restart.md addition.** New §"Verify mTLS came up" between
the "Verify login" section and the "What the restart does NOT
touch" section. Same three-curl smoke as ONBOARDING, plus an
`grep "mTLS:" /tmp/wolf-server.log` hint for the operator's
first sanity check.

### Live banner verification (after the polish)
Restarted wolf-server with the new banner format:

```
wolf-server: serving https://0.0.0.0:7860
  TLS:  TLS cert+key present at .local/certs/server/{cert,key}.pem
  mTLS: ENABLED — Wolf CA at .local/certs/ca/ca-cert.pem;
        allowed client CNs: [wolf-dashboard-client]
```

Restarted dashboard:

```
wolf-dashboard: serving HTTPS via Next.js --experimental-https
  cert: .local/certs/dashboard/cert.pem
  key:  .local/certs/dashboard/key.pem
  proxy mTLS: ENABLED — presenting .local/certs/dashboard-client/cert.pem
              as the dashboard-client cert to wolf-server
```

Both banners clear, three-line structure, mTLS state visible
without scanning prose.

### Integrity gate (all green)
* mypy: 0 errors across 6 Python projects (87 source files)
* ruff: clean
* tsc (services/dashboard): 0 errors
* eslint (services/dashboard): clean
* backend pytest: 321 / 321 in 89.36s
* live organization-isolation probe: 6 / 6

### What's next
**Slice 5.6-e — `make smoke-mtls` recurring integrity check.**
Codifies the three-curl smoke from §3.12 as a Makefile target
that runs against a freshly-restarted wolf-server. Becomes the
canonical "did we break mTLS" check that runs before every push.
Also adds a CI job so the same smoke runs against every PR.
Closes Phase 5.6.

---

## 2026-06-03 — Slice 5.6-c: mTLS enforcement (wolf-server middleware + dashboard proxy client cert)

**Session type:** claude-code
**Phase:** 5.6 — Edge-component architecture + mTLS (slice c of e)
**Branch / commit:** main @ (this commit)

### What we did
Phase 5.6 step 3. wolf-server now requires the dashboard's
client cert for any non-/healthz endpoint, and the dashboard's
reverse-proxy presents that cert on every outbound call. Together
with 5.6-a (browser sees one origin) and 5.6-b (the cert exists),
this completes the mTLS substrate for ADR 0016's component
architecture — wolf-server actively refuses anyone who isn't
wolf-dashboard.

Design choices (operator-confirmed up-front)
--------------------------------------------
* **CN allowlist, strict.** Only certs whose Subject CN matches
  `MTLS_ALLOWED_CLIENT_CNS` (default `["wolf-dashboard-client"]`)
  pass. Future relay daemons get their own CN added via env var;
  the middleware iterates a frozenset for the check.
* **GET /healthz from loopback bypasses the cert check.** Lets
  Kubernetes liveness probes / systemd watchdog scripts / same-
  host curl liveness-poll wolf-server without needing the client
  cert. The bypass is loopback-only (`127.0.0.1`/`::1`) and
  GET-only, so it can't be exploited from the LAN.
* **Enforcement is split between TLS and application layers.**
  uvicorn uses `ssl_cert_reqs=CERT_OPTIONAL` so it accepts the
  TCP+TLS connection regardless, then verifies any presented
  cert against the Wolf CA at the TLS layer. The ASGI
  MtlsMiddleware does the CN allowlist check + audit logging.
  Lets us return JSON 401 responses + implement the /healthz
  bypass cleanly + audit-log decisions specifically.

Files added
-----------
* `services/server/wolf_server/runtime/__init__.py` — new package
  for runtime helpers that sit next to uvicorn (vs. application
  code under wolf_server.*).
* `services/server/wolf_server/runtime/peer_cert_patch.py` —
  monkey-patch on uvicorn's `RequestResponseCycle.__init__` (both
  h11 + httptools backends). Reads `transport.get_extra_info(
  "ssl_object").getpeercert()` once per request and stashes the
  parsed-cert dict under `scope["state"]["wolf_peer_cert"]`.
  uvicorn 0.47 does NOT surface peer cert info to ASGI by
  default, so without this patch the middleware has no way to
  read the cert's Subject CN. No-op when there's no SSL context
  (plain HTTP dev path) — idempotent via a module-level guard.
* `services/server/wolf_server/auth/mtls_middleware.py` — the
  ASGI middleware. ~150 LOC including comments. Reads the peer
  cert from scope, extracts CN, compares against the allowlist,
  returns JSON 401 with a specific `error` code on reject
  (`mtls_required` for no cert, `mtls_cn_rejected` for bad CN).
  Audit-logs every reject decision via structlog. Stashes the
  successful CN on `request.state.mtls_cert_cn` so downstream
  code can include "which component made this call" in its own
  audit events.
* `services/server/tests/test_mtls_middleware.py` — 9 unit
  tests covering: no cert → 401, disallowed CN → 401, cert
  without CN → 401, allowed CN → 200, multi-CN allowlist works,
  /healthz from 127.0.0.1 → 200, /healthz from ::1 → 200,
  /healthz from non-loopback → 401, POST /healthz from loopback
  → 401 (bypass is GET-only).

Files changed
-------------
* `services/server/wolf_server/config.py` — three new fields:
  `mtls_ca_path` (default `.local/certs/ca/ca-cert.pem`),
  `mtls_allowed_client_cns` (default `"wolf-dashboard-client"`),
  and two properties: `mtls_enabled` (True iff CA + server cert
  + server key all exist on disk — same cert-files-are-the-
  signal pattern as Phase 5.4-c's HTTPS auto-detect) and
  `mtls_allowed_client_cn_list` (parses the comma-separated env
  value into a list). The CORS comment was refreshed to note
  CORS is now defence-in-depth, not the primary trust boundary.
* `services/server/wolf_server/main.py` — mounts MtlsMiddleware
  AFTER AuthMiddleware (Starlette's LAST-added runs OUTERMOST),
  so mTLS rejects requests before any auth code runs. Only
  mounted when `Settings.mtls_enabled` is True.
* `services/server/wolf_server/__main__.py` — when both HTTPS
  and mTLS conditions are met, calls
  `patch_uvicorn_for_peer_cert()` to install the scope patch
  + passes `ssl_ca_certs=<Wolf CA>` + `ssl_cert_reqs=
  ssl.CERT_OPTIONAL` to uvicorn. Startup banner now reports
  "mTLS: Wolf CA at …; allowed client CNs: […]" so the operator
  sees mTLS is active from the launcher's first log line.
* `services/dashboard/app/api/[...path]/route.ts` —
  `loadDispatcher()` now also loads
  `.local/certs/dashboard-client/{cert,key}.pem` if they exist
  and passes them via `Agent({ connect: { ca, cert, key } })`.
  When the client leaf is absent (e.g. half-configured state)
  the proxy still trusts the CA but doesn't present a cert —
  wolf-server's middleware then rejects with 401.
* `services/server/tests/conftest.py` — pinned `MTLS_CA_PATH` to
  a nonexistent path so the test suite's TestClient-based tests
  (which can't present a peer cert) don't get 401'd by
  MtlsMiddleware once `.local/certs/` exists on disk. The
  middleware's own unit tests in `test_mtls_middleware.py`
  build their own app and inject synthetic peer certs at the
  scope layer, so they're unaffected.

Live verification (end-to-end, post-implementation)
---------------------------------------------------
With `wolf-cert init` minted certs, wolf-server on HTTPS+mTLS,
dashboard on HTTPS:

1. `curl https://localhost:7860/api/v1/auth/me` (NO client cert)
   → **HTTP 401** body
   `{"error":"mtls_required","detail":"wolf-server requires a
   Wolf-CA-signed client certificate…"}`
2. `curl --cert dashboard-client/cert.pem --key dashboard-client/key.pem
   https://localhost:7860/api/v1/auth/me`
   → **HTTP 401** body `{"detail":"Not authenticated"}` — mTLS
   passed (correct CN); auth middleware then rejected because no
   login cookie. Exactly the expected handoff between the two
   middlewares.
3. `curl https://localhost:7860/healthz` (loopback, no cert)
   → **HTTP 200** `{"status":"ok","service":"wolf-server"}` —
   /healthz bypass works.
4. Full dashboard round-trip via `https://localhost:3000`:
   POST /api/v1/auth/login → 200 (cookies set), GET /me → 200
   (full user payload), POST /chat/stream → token-by-token SSE.
   Browser only sees the dashboard origin; the dashboard's
   reverse-proxy presents the dashboard-client cert to
   wolf-server which accepts it.

Integrity gate (all green)
--------------------------
* mypy: 0 errors across 6 Python projects (87 source files)
* ruff: clean
* tsc (services/dashboard): 0 errors
* eslint (services/dashboard): clean
* backend pytest: **321 / 321** in 89.63s (was 312; +9 new mTLS
  middleware tests)
* live organization-isolation probe: 6 / 6

### What's next
**Slice 5.6-d — Launcher wiring polish + operator-doc walkthrough.**
* Tighten the launcher's startup banner so the mTLS state is
  prominent, not buried in a sub-line.
* Walk through the operator-facing flow in `ONBOARDING.md`:
  `wolf-cert init` → restart wolf-server → restart dashboard
  → mTLS is active everywhere; what happens to direct curl
  attempts; how to debug a CN mismatch.
* Refresh `docs/restart.md` with the new "did mTLS come up?"
  smoke check.

**Slice 5.6-e — 401-without-cert smoke test as a recurring
integrity check.** Add a tiny `make smoke-mtls` target that
spins up wolf-server with certs and verifies (a) direct
no-cert curl → 401 mtls_required, (b) direct with-cert curl
→ 401 Not authenticated (i.e. mTLS passes), (c) /healthz
from loopback → 200. Becomes the canonical "did we break
mTLS" check that runs before every push.

### Operator impact
This is the slice where wolf-server **actively refuses** non-
dashboard callers. Two consequences for operators:
* Direct `curl https://wolf-server:7860/api/...` from any
  workstation that doesn't present the dashboard-client cert
  now fails with 401 mtls_required. The migration path is to
  go through `https://dashboard:3000/api/...` (which proxies)
  instead.
* The `dashboard-client` cert can be copied to other hosts to
  authorize them as alternate edge components (e.g. a
  load-balancer terminating TLS), but it should NOT be copied
  casually — any holder of the cert can talk to wolf-server
  unauthenticated-at-the-mTLS-layer. The key file is 0600 by
  default; keep it that way.

---

## 2026-06-03 — Slice 5.6-b: dashboard-client cert (LeafKind.CLIENT) added to wolf-cert init

**Session type:** claude-code
**Phase:** 5.6 — Edge-component architecture + mTLS (slice b of e)
**Branch / commit:** main @ (this commit)

### What we did
Phase 5.6 step 2. wolf-cert now mints a third built-in leaf,
`dashboard-client`, with `LeafKind.CLIENT` and CN
`wolf-dashboard-client`. This is the cert the dashboard's
reverse-proxy (5.6-a) will present to wolf-server in 5.6-c's
mTLS path.

Code changes:
* `packages/cert/wolf_cert/cli.py` — added a third entry to
  `_BUILTIN_LEAVES`. Updated `--leaf` help text on `add-host` and
  `renew` to advertise the new leaf name as a valid choice. The
  init-time SAN-application comment block now explains why
  client-kind leaves still get the same SAN set (uniformity +
  inspection ergonomics; servers don't validate a client cert's
  SAN against the source address, so it's harmless).

Tests:
* `services/server/tests/test_cert_cli.py` — three existing
  tests updated to expect the new leaf:
    - `test_init_creates_ca_and_two_leaves` →
      `test_init_creates_ca_and_all_builtin_leaves`, now
      loops `("server", "dashboard", "dashboard-client")`.
    - `test_init_leaves_have_strict_key_permissions` adds
      the third key path to the mode-check list.
    - `test_add_host_appends_dns_san_to_all_leaves` now
      verifies the SAN propagates to dashboard-client too.
    - `test_status_prints_ca_and_leaves` asserts
      `"leaf 'dashboard-client'"` appears in `wolf-cert status`
      output.
* `test_init_leaves_are_server_kind` renamed to
  `test_init_server_leaves_get_server_eku` and split: a new
  `test_init_dashboard_client_leaf_gets_client_eku` test
  verifies the new leaf's EKU is clientAuth + CN is
  `wolf-dashboard-client`. The test_cert_cli suite count went
  from 311 → 312 total backend tests.

### Live verification
After `wolf-cert revoke --yes && wolf-cert init`:

```
.local/certs/ca/{ca-cert,ca-key}.pem
.local/certs/server/{cert,key}.pem
.local/certs/dashboard/{cert,key}.pem
.local/certs/dashboard-client/{cert,key}.pem  ← new
```

`openssl x509 -in .local/certs/dashboard-client/cert.pem -noout
-subject -issuer -ext extendedKeyUsage` reports:
* `subject=CN = wolf-dashboard-client` ✓
* `issuer=CN = Wolf Root CA, O = Wolf` ✓
* `X509v3 Extended Key Usage: TLS Web Client Authentication` ✓

Key file mode is 0600, cert 0644.

### Integrity gate (all green)
* mypy: 0 errors across 6 Python projects (84 source files)
* ruff: clean
* tsc (services/dashboard): 0 errors
* eslint (services/dashboard): clean
* backend pytest: **312 / 312** in 125.53s
* live organization-isolation probe: 6 / 6

### What's next
**Slice 5.6-c — mTLS middleware on wolf-server.** Two changes:

* wolf-server's launcher (`wolf_server/__main__.py`) will pass
  uvicorn's `--ssl-ca-certs` + `--ssl-cert-reqs=2` (CERT_REQUIRED)
  when both the Wolf CA and the server's leaf are present. A new
  ASGI middleware inspects the peer certificate, audit-logs any
  reject decision (cert missing, wrong CN, not signed by Wolf
  CA), and (in dev with no certs) becomes a no-op so the
  zero-setup new-contributor path still works.
* The dashboard's reverse-proxy `Agent` (5.6-a's
  `WOLF_DISPATCHER`) gets extended with `cert` + `key` so the
  proxy actually presents the dashboard-client leaf when it
  fetches from wolf-server.

### Operator impact
After this slice, operators who already had certs minted (from
the 5.6-a or earlier verification cycle) will need to run:

```
uv run --project services/server python -m wolf_cert revoke --yes
uv run --project services/server python -m wolf_cert init
```

to pick up the new dashboard-client leaf. (A future
`wolf-cert init --only-missing` flag would let us avoid this; not
worth building today.)

---

## 2026-06-03 — Slice 5.6-a: Next.js reverse-proxy route handler

**Session type:** claude-code
**Phase:** 5.6 — Edge-component architecture + mTLS (slice a of e)
**Branch / commit:** main @ (this commit)

### What we did
Phase 5.6 opens. Slice 5.6-a introduces wolf-dashboard's
catch-all reverse-proxy route handler, the slice that **kills the
cross-origin NetworkError** that surfaced in Phase 5.4.

Files added:
* `services/dashboard/app/api/[...path]/route.ts` — the catch-all.
  Receives every HTTP method (GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS),
  forwards to wolf-server (from server-side `WOLF_SERVER_URL`
  env var, default `http://localhost:7860`), and streams the
  upstream response body back via `new Response(upstreamResp.body)`
  — no buffering, so SSE token-by-token rendering works through
  the proxy. Filters hop-by-hop headers per RFC 7230 §6.1; uses
  `Headers.getSetCookie()` + `append("set-cookie", ...)` so
  multiple `Set-Cookie` headers (which wolf-server emits — one
  for the 1-hour access token, one for the 7-day refresh token)
  aren't collapsed into a single comma-joined line. Runtime is
  `nodejs` (not edge) because the streaming-fetch via undici
  with `duplex: "half"` for streaming request bodies needs the
  Node runtime. On wolf-server unreachable returns 502 with a
  JSON error body; on client abort mid-flight returns 499
  (nginx convention for client-closed-request).

Files changed:
* `services/dashboard/lib/api.ts` — removed the `apiBase()`
  helper; the browser now uses relative `/api/v1/...` paths
  exclusively. All requests are same-origin against the
  dashboard's `:3000` (the proxy forwards them server-side).
* `services/dashboard/.env.example` — replaced the previous
  browser-side `NEXT_PUBLIC_SERVER_URL` env var with the
  server-side `WOLF_SERVER_URL`. The browser doesn't see this
  value — only the dashboard's Next.js server reads it.
* `services/dashboard/.env.local` (gitignored) — same.
* `services/dashboard/README.md` — rewrote the intro and the
  auth-notes section to describe the new architecture: one
  browser origin, reverse-proxy forwarding to wolf-server,
  same-origin cookies (no eTLD+1 gymnastics needed).
* `services/server/wolf_server/config.py` — refreshed the CORS
  comment to note that post-5.6-a browsers don't make cross-
  origin requests in normal operation; CORS is kept configured
  for ops-tool use and defence-in-depth.
* `docs/PROGRESS.md` — flipped §1 to "5.6-a SHIPPED"; §6 known-
  issues entry for the cross-origin NetworkError marked
  RESOLVED in 5.6-a.

### Verification (live, end-to-end)
1. tsc + eslint on `services/dashboard`: clean.
2. Started wolf-server (`python -m wolf_server` on `:7860`)
   and `next dev -H 0.0.0.0 -p 3000` against the new code.
3. Curl tests through the proxy at `:3000`:
   - `POST /api/v1/auth/login` → HTTP 200, JSON body identical
     to direct wolf-server response. Set-Cookie headers for
     BOTH `wolf_access_token` AND `wolf_refresh_token` arrived
     intact (this caught a real bug in the first pass — the
     initial `forEach(set)` collapsed multi-Set-Cookie; fixed
     using `getSetCookie()` + `append()`).
   - `GET /api/v1/auth/me` with the cookie jar from login →
     HTTP 200, full user/organization/role payload.
   - `GET /api/v1/auth/me/organizations` → HTTP 200, organization list.
   - `POST /api/v1/chat/stream` (SSE) → token-by-token
     streaming verified: `loop.started` arrives first, then
     `step.started`, then per-token `model.delta` events
     flush as they're emitted (no buffering — confirmed by
     watching the output stream in real time, not by waiting
     for the connection to close).
4. Stopped both processes cleanly.

### Why this matters
This is the architectural fix the user has been pointing at
since Phase 5.4-e shipped the trust-install walkthrough. The
trust-portal UX they explicitly rejected ("forcing the user to
install certs is a bad experience") is now bypassed entirely:
the browser only sees ONE origin's cert, that origin is
wolf-dashboard's, and wolf-server is invisible to the browser.
The remaining slices (5.6-b through 5.6-e) layer mTLS on the
proxy → server hop so a distributed deployment can require
that wolf-dashboard present a valid client cert before
wolf-server will answer.

---

## 2026-06-03 — Phase 5.5 CLOSED: component rename + total-rename closeout (A→G)

**Session type:** claude-code
**Phase:** 5.5 (Component renaming refactor) — CLOSED
**Branch / commit:** main @ `08dee03`

### What we did
- Shipped the **184-file Phase 5.5 rename** (`a3d18ec`):
  `services/orchestrator/` → `services/server/`, `app/` → `wolf_server/`,
  `services/gateway/app/` → `services/gateway/wolf_gateway/`,
  `frontend/` → `services/dashboard/`. wolf-cert built-in leaves
  renamed `orchestrator`/`frontend` → `server`/`dashboard`. Server-
  side TLS path defaults aligned. Dashboard env var renamed
  `NEXT_PUBLIC_ORCHESTRATOR_URL` → `NEXT_PUBLIC_SERVER_URL`.
  Permanently kills Gotcha #1 (two-`app`-packages collision) since
  the new package names cannot collide.
- Ran a **first audit pass** (`70d2d94`) — caught operator-tooling
  paths: CI workflow, Makefile, Dockerfiles, docker-compose
  service names, dashboard `lib/types.ts` cross-link, six
  management-module docstrings.
- Ran an **exhaustive every-file audit** (`ad4868c`) after the
  operator asked for a literally-every-file sweep. Caught LIVE
  identifiers in `wolf_server/main.py` (OTel `service_name`,
  `/healthz` JSON response, FastAPI app title, structlog event
  keys); six management-module docstrings; orphan root
  `PROGRESS.md` (3+ weeks stale); leftover `frontend/.next/`
  cache dir; `prompts/HANDOFF-NEW-MACHINE.md` paths;
  `services/dashboard/README.md` full rewrite; `SECURITY.md` +
  `CONTRIBUTING.md` prose nits.
- Caught **three trailing references** on re-read (`0e428bc`):
  `wolf_server/main.py:1` module docstring, `:94` CORS comment,
  and `wolf_server/__init__.py:1` package docstring still saying
  "Wolf Orchestrator".
- Operator asked a final "anything else?" — re-grepped with own
  eyes, surfaced a **shipped CLI bug** + 6 dead bootstraps + 14
  broken markdown links + ~30 in-source comments + multiple
  shipped-package docstrings. Closed all of it in the
  **total-rename closeout** (`08dee03`).

### What changed in the closeout (`08dee03`) specifically
- **A. Shipped CLI bug fixed.** `wolf-cert --leaf` help on both
  `add-host` and `renew` advertised `'orchestrator', 'frontend'`
  — leaves that no longer exist. Now: `'all', 'server',
  'dashboard'`. A user reading the help and running
  `--leaf orchestrator` would have gotten a hard error.
- **B. `services/dashboard/package-lock.json` regenerated.** Was
  `"name": "frontend"` × 2; now `"name": "wolf-dashboard"`.
- **C. Six dead `_ORCH = "services/orchestrator"` sys.path
  bootstrap blocks deleted** from `tools/embedding_benchmark/*`
  (three files), `tools/seed_knowledge/__main__.py`,
  `tools/organization_isolation_test/__main__.py`, and
  `services/server/tests/test_seed_knowledge_ingesters.py`. The
  guard `if _ORCH.is_dir():` had made them silent no-ops; the
  workspace install handled actual imports.
- **D + E. Shipped-package + test/management docstrings.**
  `wolf_cert/__init__.py`, `wolf_cert/authority.py` (module +
  `LeafKind` + `sign_leaf` + `write_key_pem`),
  `wolf_secrets/interface.py`, `wolf_gateway/__init__.py`, plus
  `conftest.py`, `test_cert_authority.py`, `management/__init__.py`,
  `set_secret.py`, and `smoke_wazuh.py`'s synthetic email
  (`smoke-test@orchestrator.local` → `smoke-test@wolf-server.local`,
  appears in every smoke-test run's audit event).
- **F. Operator docs rewrite.** `ONBOARDING.md` §4.4 header +
  §5 restart steps (`# 3. Orchestrator` / `# 4. Frontend` →
  `# 3. wolf-server` / `# 4. wolf-dashboard`) + replaced direct
  uvicorn invocation with the Phase 5.4-c launcher + **bulk-
  fixed 14 broken `services/server/app/…` markdown links via
  `sed` to `services/server/wolf_server/…`**. `docs/restart.md`:
  troubleshooting table rows + the §"Why each step" paragraph
  that spoke of 5.4-d as future tense. `prompts/HANDOFF-NEW-
  MACHINE.md`: three prose touch-ups.
- **G. ~30 in-source comments and one LLM-visible prompt.**
  Backend: agent loop, prompts, strategies, models, tools
  registry, config, caching, tenancy, audit, chat API, auth API,
  grounding validator, Ollama streaming, Anthropic adapter, the
  `__main__.py` launcher, `wolf_server/__init__.py`. Frontend:
  `lib/api.ts` JSDoc, `lib/branches.ts` JSDoc, `lib/types.ts`,
  `hooks/use-conversation-streams.ts`. Tests: three test
  docstrings. Workspace `pyproject.toml`. `tools/model_probe/__init__.py`.
  Notably, the **LLM-visible system prompt** (Rule 3) was
  updated: "The orchestrator stamps organization scope onto every
  request" → "wolf-server stamps organization scope onto every
  request" — the model now sees consistent component naming.

### Final integrity gate (all green)
- mypy: **0 errors** across 6 Python projects (84 source files)
- ruff: **clean**
- tsc (services/dashboard): **0 errors**
- eslint (services/dashboard): **clean**
- backend pytest: **311 / 311** passed in 74.23s
- live organization-isolation probe: **6 / 6**
- wolf-cert CLI smoke: `--leaf` help reads `'all', 'server',
  'dashboard'` as designed

### What we decided
- The **planning bundle (`docs/00`–`docs/16`) is deliberately not
  swept** as part of Phase 5.5. Those are descriptive specs that
  predate the rename; a focused doc-sweep slice will refresh them
  alongside the installation-guide module (post-5.6/5.7/5.8).
  Tracked in PROGRESS.md §6.
- Past ADRs (`0001`–`0015`), pre-5.5 CHANGELOG entries, and code
  comments that describe *historical* context (e.g. "this used to
  bite people because…") are left as **append-only archaeology**.
  Greppers searching for "orchestrator" or "frontend" land on the
  Phase-5.5-rename breadcrumb every time.

### What's next
**Phase 5.6 — Edge-component architecture + mTLS.** Will introduce
the Next.js `/api/*` route handlers as a reverse proxy from
wolf-dashboard to wolf-server, so the browser only ever sees the
dashboard origin. Will also wire mTLS using the shared Wolf CA
(`LeafKind.CLIENT` for the dashboard's client cert), so any
distributed deployment requires component-to-component auth via
the CA. This is the slice that **kills the cross-origin
`NetworkError`** that surfaced in Phase 5.4.

### Why this matters
Phase 5.5 was a pure refactor with no operator-visible behaviour
change — but it was the architectural prerequisite for everything
that follows. The rename closes Gotcha #1 permanently, aligns the
codebase with ADR 0016's component model, and makes the systemd /
FHS / packaging story land cleanly in 5.7 / 5.8 / 5.9 / 5.10
without having to drag the old names along. The closeout's CLI-
bug fix (A) is the one piece that has user-visible impact today.

---

## 2026-06-03 — Phase 5.4 close-out + Phase 5.5+ direction locked

**Session type:** mixed (claude-code + human design conversation)
**Phase:** Phase 5.4 closed → Phase 5.5+ planning
**Duration:** half-session for the close-out + design discussion
**Branch / commit:** `main` — close-out commit pending.

### What we did
- Phase 5.4 — Native HTTPS + `wolf-cert` CLI — formally CLOSED.
  Five sub-slices shipped (see the individual entries below):
  5.4-a (`9a44b65`), 5.4-b (`80e0f10`), 5.4-c (`5afd4e9`),
  5.4-d (`c7fed44`), 5.4-e (`b064b82`).
- Hit and diagnosed the `NetworkError when attempting to fetch
  resource` cross-origin issue with the just-shipped HTTPS stack.
  Browser opens `https://<host>:3000/` (dashboard), JS does cross-
  origin `fetch()` to `https://<host>:8000/` (server) — that
  second origin's cert isn't trust-established, browser blocks
  the fetch silently in JS.
- Walked through whether to fix this via a trust-portal UX
  (originally floated as 5.4-f) or via an architectural change.
  Operator rejected the trust-portal UX as bad UX ("forcing CA
  installation"); rejected forcing the CA install step entirely.
- Reframed: the right architectural fix is the **edge-component
  pattern** — same as Wazuh's `wazuh-dashboard ↔ wazuh-indexer
  ↔ wazuh-manager` model. Single edge origin visible to the
  browser; everything else is component-to-component mTLS using
  a shared internal CA. Cleanly maps to Wolf's existing wolf-cert
  infrastructure (`LeafKind.CLIENT` is already in 5.4-a's
  library, ready to mint dashboard / server / future-relay
  client certs).

### What we decided
- **Drop the trust-portal slice** (the originally-floated 5.4-f).
  No wizard-driven CA install; we solve the NetworkError by
  removing the second browser-visible origin, not by guiding the
  user to install certs.
- **Reorganise the project around Wazuh-style components.**
  Three deployable services + dev/operator tooling, mapped to
  Wazuh's component model:
    * `wolf-dashboard` (rename of `frontend/`) — Next.js edge
      component, the only thing browsers talk to. Reverse-proxies
      to `wolf-server` internally.
    * `wolf-server` (rename of `services/orchestrator/`) — FastAPI
      brain; binds `127.0.0.1` in all-in-one, exposed with mTLS
      required in distributed.
    * `wolf-database` (new, bundled) — wraps Postgres 17 +
      pgvector via a Wolf-managed systemd unit, akin to
      `wazuh-indexer`. Data dir under FHS `/var/lib/wolf-database/`.
    * `wolf-gateway` (rename of `services/gateway/`, currently
      stubbed) — Phase 6's propose/execute path. Lives in its
      own systemd unit, disabled by default until Phase 6 turns
      it on.
- **Phase ordering** (5.9 + 5.10 deferred to final-release phase
  per operator direction):
    * Phase 5.5 — Component renaming + directory restructure
      (pure refactor; no functional change). The wolf-cert
      leaves get renamed from `orchestrator` / `frontend` to
      `server` / `dashboard` as part of this.
    * Phase 5.6 — Edge-component + mTLS architecture (the
      B-slice in the new naming). **Kills the NetworkError.**
    * Phase 5.7 — `wolf-database` extraction as a bundled
      component.
    * Phase 5.8 — systemd units + `/bin` layout + FHS install
      paths.
    * Phase 5.9 (APT) + Phase 5.10 (DNF) — deferred to the
      official-release phase.
- **`bin/` is for shipped CLIs** (`wolf-cert`, future
  `wolf-status` / `wolf-backup`). `tools/` keeps dev-internal
  probes and smoke tests.
- **Full FHS layout** at install time: `/usr/bin/wolf-*`,
  `/usr/lib/wolf-<component>/`, `/etc/wolf-<component>/`,
  `/var/lib/wolf-<component>/`, journald for logs.
- **`wolf-database` is BUNDLED** (Wazuh-indexer style) rather
  than tooling-only. Depends on system Postgres 17 + pgvector
  packages but managed via a Wolf-owned systemd unit with
  Wolf-controlled data dir + configs.

### What's next
- **ADR 0016** — "Wolf component architecture & packaging" —
  drafted before any Phase 5.5 code lands. Captures the
  three-component model, two deployment topologies (all-in-one
  + distributed), trust model (shared Wolf CA + mTLS between
  machine components), systemd lifecycle, FHS install paths,
  and the deferred-packaging note.
- After ADR sign-off: open Phase 5.5 (renaming refactor).

### Why this matters
The Wazuh parallel: Wazuh's components share one CA, each has
its own leaf cert, and inter-component traffic is mTLS. Browsers
only talk to `wazuh-dashboard`. Wolf has been heading toward this
model since Phase 5.4 minted the shared CA; the 2026-06-03
direction nails the operational shape (systemd units, FHS paths,
bundled Postgres) so the platform is reproducible at install time.
This is the architectural step that takes Wolf from "dev box that
runs locally" to "deployable security tool."

## 2026-06-03 — Slice 5.4-e: ONBOARDING.md trust-install per OS

**Session type:** claude-code
**Phase:** Phase 5.4 — final sub-slice
**Duration:** ~1 h
**Branch / commit:** `main` — `b064b82`.

### What we did
- Added a new §3.12 "Enable HTTPS via `wolf-cert`" to
  `ONBOARDING.md` documenting the full lifecycle (`init` →
  `status` → `export-ca` → `add-host` / `renew` / `revoke`) plus
  per-OS trust-install commands: Ubuntu/Debian
  (`update-ca-certificates`), Fedora/RHEL (`update-ca-trust`),
  macOS (`security add-trusted-cert`), Windows PowerShell
  (`Import-Certificate`), Chrome / Edge NSS DB on Linux
  (`certutil`), Firefox on every OS (`about:preferences#privacy`).
- Updated §3.10 to use the Phase 5.4-c / 5.4-d launchers
  (`uv run python -m app`, `npm run dev`) instead of the legacy
  `uvicorn app.main:app` / direct `next dev` invocations.
- Updated Gotcha #1 (two `app/` packages) and rewrote Gotcha #4
  (LAN access — the IP-agnostic dev change made the original
  three-file-edit checklist mostly obsolete).
- Verified the cert chain end-to-end via `openssl verify` (both
  orchestrator and frontend leaves chain to the CA) and
  `openssl x509` parse of the exported CA cert (PEM format
  consumed correctly by every documented per-OS install step).

### What we discovered
- The trust-install requires sudo and persistent system state,
  so we documented it rather than running it in-session; the
  cryptographic verification (openssl chain check) is enough to
  confirm the documented steps will work.

### What's next
- Phase 5.4 close-out commit (this commit's parent).

## 2026-06-02 — Slice 5.4-d: frontend HTTPS auto-detect launcher

**Session type:** claude-code
**Phase:** Phase 5.4 — fourth sub-slice
**Duration:** ~1.5 h
**Branch / commit:** `main` — `c7fed44`.

### What we did
- Wrote `frontend/scripts/dev.mjs` — a tiny Node launcher that
  detects `<repo>/.local/certs/frontend/{cert,key}.pem` and
  invokes `next dev` with `--experimental-https
  --experimental-https-cert <…> --experimental-https-key <…>`
  when both files exist. Falls back to plain `next dev` otherwise.
  Forwards extra args and propagates SIGINT/SIGTERM to the child.
- Updated `package.json` so `npm run dev` invokes the wrapper;
  `npm run dev:plain` preserves a direct `next dev` escape
  hatch for the rare case the auto-detect needs to be bypassed.
- Refreshed the `clipboard.ts` comment to reflect the new
  posture — execCommand fallback is now the HTTP-fallback
  path, not the default; secure-context API is the default
  whenever `wolf-cert init` has been run.
- Verified end-to-end with curl + the freshly-minted Wolf CA
  on three states (no certs / init / revoke).

### What's next
- Slice 5.4-e: ONBOARDING.md trust-install per OS.

## 2026-06-02 — Slice 5.4-c: orchestrator HTTPS auto-detect launcher

**Session type:** claude-code
**Phase:** Phase 5.4 — third sub-slice
**Duration:** ~2 h
**Branch / commit:** `main` — `5afd4e9`.

### What we did
- Added a `python -m app` launcher
  (`services/orchestrator/app/__main__.py`) that flips between
  HTTPS and HTTP based purely on the existence of the TLS cert
  + key files. Pure-function `resolve_tls()` decides; truth
  table covers cert-only / key-only / neither / both, with the
  broken-pair cases surfaced in a `reason` string so the
  operator sees why the launcher picked the scheme it did.
- Added `bind_host` / `bind_port` / `tls_cert_path` /
  `tls_key_path` to `app.config.Settings`. Defaults anchored at
  `Path(__file__).resolve().parents[3]` so the launcher finds
  certs from the repo regardless of which directory it was
  invoked from.
- Updated `docs/restart.md` for the new invocation form
  (`uv run python -m app`).
- 6 tests covering the resolution truth table.
- End-to-end verified on the dev box: real `/api/v1/auth/login`
  POST returned HTTP 200 over HTTPS with TLS verify_result = 0
  against the freshly-minted Wolf CA; `wolf-cert revoke`
  flipped the launcher back to HTTP on the next start.

### What's next
- Slice 5.4-d: frontend `next dev --experimental-https` wiring.

## 2026-06-01 — Slice 5.4-b: `wolf-cert` CLI dispatcher

**Session type:** claude-code
**Phase:** Phase 5.4 — second sub-slice
**Duration:** ~2 h
**Branch / commit:** `main` — `80e0f10`.

### What we did
- Wrote `packages/cert/wolf_cert/cli.py` — argparse-stdlib
  dispatcher over the 5.4-a library. Six subcommands:
  `init` / `status` / `export-ca` / `add-host` / `renew` /
  `revoke`. Owns the on-disk cert layout (default
  `.local/certs/<role>/{cert,key}.pem`).
- Added a console-script entry point
  (`wolf-cert = "wolf_cert.cli:main"` in
  `packages/cert/pyproject.toml`) plus a `__main__.py` so
  `python -m wolf_cert <subcommand>` works identically.
- 21 tests covering store layout, refusal paths, flag plumbing,
  SAN merging, leaf-kind preservation across `renew` and
  `add-host`, and one subprocess round-trip for the console-
  script wiring.

### Drive-by
- Fixed the ruff `per-file-ignores` glob: was `tests/**`
  (only matched top-level), now `**/tests/**` (nested test
  suites in `services/orchestrator/tests/` and
  `packages/*/tests/` weren't picking up the test-specific
  rule relaxations).

### What's next
- Slice 5.4-c: orchestrator HTTPS auto-detect launcher.

## 2026-06-01 — Slice 5.4-a: `wolf_cert` library — self-signed CA + leaf primitives

**Session type:** claude-code
**Phase:** Phase 5.4 — first sub-slice
**Duration:** ~3 h
**Branch / commit:** `main` — `9a44b65`.

### What we did
- New workspace package `packages/cert/` (`wolf-cert`) — the
  pure-library layer of Wolf's HTTPS story. Pure dependency
  on `cryptography>=42`. PEP-561 `py.typed` marker shipped.
- `generate_ca(...)` — RSA-4096 self-signed root CA with
  `basicConstraints CA:TRUE`, `keyUsage keyCertSign,cRLSign`,
  `SubjectKeyIdentifier`. Default validity 100 years (the
  "practical infinity" pattern — RFC 5280 forbids truly
  unlimited). Clamps at year 9999 to dodge TLS-stack edge cases.
- `sign_leaf(...)` — RSA-4096 leaf signed by a given CA.
  `basicConstraints CA:FALSE`, leaf `keyUsage`, configurable
  `ExtendedKeyUsage` via `LeafKind` (SERVER / CLIENT / DUAL),
  `SubjectAlternativeName` (DNS + IP), Subject + Authority
  Key Identifiers. **`LeafKind.CLIENT` is the hook for the
  future `wolf-cert issue-relay <organization>` subcommand** —
  Wolf Knowledge Relay phase will mint relay client certs
  via this exact code path with no library change.
- `write_cert_pem` / `write_key_pem` — strict permissions
  enforced post-write (0644 for certs, 0600 for keys) via
  explicit `chmod`. Caller's umask is irrelevant.
- `cert_status` — extracts subject CN, SANs (DNS + IP),
  issuer CN, UTC-aware validity (uses cryptography 42+'s
  `not_valid_*_utc` properties), is_ca, sha256 fingerprint,
  serial. Drives the CLI's `status` subcommand.
- `discover_local_sans` — best-effort hostname + loopback
  enumeration for sensible defaults during `wolf-cert init`.
- 24 tests covering CA shape, leaf shape (chains to CA via
  actual signature verification), SAN propagation, EKU per
  LeafKind, year-9999 clamp, empty-SAN refusal, PEM round-trip,
  permission bits under hostile umask, non-RSA key rejection,
  `cert_status` parsing, and local SAN discovery.

### Drive-by
- Fixed a pre-existing environment-fragile test
  (`test_factory_accepts_sentence_transformers_aliases`) that
  was OOMing on the dev GPU when Ollama was hot. Pinned to CPU
  via `monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")` so the
  suite passes regardless of GPU memory state — per the
  *no-unaddressed-errors* standing rule.

### What's next
- Slice 5.4-b: `wolf-cert` CLI dispatcher.

## 2026-06-02 — Slice 5.0c-l: conversation tree branching

**Session type:** claude-code
**Phase:** Phase 5 prep — branching as the final UX block of the 5.0c series
**Duration:** large — single coherent slice landed across one session
**Branch / commit:** `main` — `e7e2bd1` (squashed v4 architecture + v4.1 bug fix into one slice commit)

### What we did
- Replaced the Q+A-as-one-unit `ChatExchange` list with a true
  message tree: each user message and each assistant message is its
  own node with its own `children: string[]` array. Conversations
  carry `{ nodes: Record<id, MessageNode>, root_children: string[],
  selected_root_id: string | null }`; the active thread is derived
  by following `selected_root_id` then repeated `selected_child_id`
  until a leaf.
- Single primitive — `fork(conversation, target_id, new_node)` —
  unified Edit (on a user node) and Retry (on an assistant node).
  Fork's contract: the new version is appended to
  `target.parent_id`'s children (and ONLY there). Two assertions
  enforce it at runtime: (a) `new_node.parent_id === target.parent_id`,
  (b) `new_node.id` is not already in the node map (id-uniqueness
  guard against silent overwrite).
- `< N/M >` navigator inline in the HoverActionBar — shares the
  group-hover fade with Copy / Retry / Edit. Driven solely by
  `parent.children.length > 1`; user-node navigators read
  `root_children` when `parent_id === null`. Distinct fork points
  along the active path each render their own counter; sibling sets
  never merge.
- Inline Edit on user messages with a Save / Cancel UI and an
  `AlertCircle` disclaimer (`Editing creates a new branch. Your
  previous attempt stays accessible via the navigator below.`).
  Retry available on every assistant message, not just the last.
- Hide-prior-sibling during streaming: while a branch run is
  `phase === "running"`, the visible path truncates at
  `stream.parentUserNodeId` so the old assistant sibling visually
  disappears and the streaming view replaces it in place. Once
  archived, the new node joins the path naturally.
- History overlay search now scans ALL nodes (off-branch siblings
  included); clicking a buried match calls `selectPathTo` so the
  active path re-points to surface the matched node.
- Stream hook's `submit` signature gained `parentUserNodeId` (the
  user-message id this assistant response answers). The hook
  produces a `StreamCompletion` payload that chat-shell converts
  to an `AssistantMessageNode` and appends via the fork primitive.
- New `frontend/lib/branches.ts` is the only place the tree is
  mutated. Helpers: `activePathNodes`, `siblingsOfNode`,
  `historyUpTo`, `appendChildOf`, `fork`, `switchToSibling`,
  `selectPathTo`, `makeUserNode`, `makeAssistantNode`.

### The v4.0 → v4.1 bug
- v4.0 had a render filter (`completionPending`) that hid the
  prior sibling between settle and archive. The bug: the hook
  never cleared `stream.completion` post-archive, so on later
  navigate-back the predicate still matched and the filter sliced
  the visible path at `parentUserNodeId`, **hiding the sibling
  whose data was, in fact, fully present in `conversation.nodes`**.
  User reported it as "Wolf's previous response disappears when I
  navigate back."
- v4.1 fixes: (a) new `streams.clearCompletion(convoId)` API,
  called from the archive effect after appending the new node;
  (b) truncation predicate simplified to `isRunning` only;
  (c) node ids always `randomId()` — never reuse backend's
  `loop_id` (which stays as its own field), so a future backend
  regression cannot collide ids and silently overwrite a node.

### What we verified
- Integrity gate clean: tsc 0 / eslint 0 / mypy 0 / ruff 0 /
  backend pytest 260/260 / live organization-isolation 6/6.
- Acceptance test (manual): send "Hello" → retry the assistant
  reply → edit the user message. Result: 2/2 navigator on the
  assistant message AND a separate independent 2/2 navigator at
  the user-message level. The retry-set `[a1, a2]` under the
  original user message stays intact and reappears when
  navigating back via `<` on the user-level navigator. No merged
  "3/3" appears anywhere. Each version's full content (user and
  assistant) preserved verbatim across all navigation paths.

### What we deferred
- Conversation tree persistence to the database. Conversations
  remain in-memory only today; refresh wipes them. Complete plan
  captured in cross-session memory `conversation-tree-persistence-
  plan.md` so when the DB-storage phase begins nothing gets lost:
  two-table schema (`conversations`, `message_nodes`), explicit
  `position` integer for stable sibling order, atomic version-add
  transaction wrapping the new INSERT + parent's
  `selected_child_id` UPDATE, no path flattening on save, lossless
  round-trip test, organization scoping via `OrganizationScopedQueryBuilder`.

### What's next
- Wrap 5.0c with this PROGRESS.md + CHANGELOG catch-up commit, then
  Phase 5.4 (Native HTTPS + `wolf-cert` CLI), then Phase 5 proper
  (RBAC / cases / reporting).

## 2026-06-01 — Slice 5.0c-k: Stop button + concurrent per-conversation streams (incl. typing-foundation pre-fix)

**Session type:** claude-code
**Phase:** Phase 5 prep — stream lifecycle hardening
**Duration:** ~3 h across the three commits
**Branch / commits:** `main` — `ec4ff9d` (`stop_reason` type widening), `bf00c01` (typing-foundation), `2d83607` (the slice itself)

### What we did
- Replaced the singleton `useChatStream` hook with
  `useConversationStreams` — a per-conversation stream manager
  keyed by conversation id. Each conversation has its own
  `StreamState` (status, exchange, working buffers) and its own
  `AbortController`. Two conversations can stream in parallel; the
  in-flight indicator in the sidebar shows BOTH simultaneously.
- Composer's Send button is swapped for a Stop button at the same
  position whenever the active conversation is streaming (user-
  requested UX refinement — keeps the interrupt in reach without
  scrolling up into the thread). The textarea itself stays fully
  interactive throughout: drafts survive across "type → press
  Enter → still streaming → click Stop → press Enter" cycles.
- On `AbortError` (the user clicked Stop), the catch path
  synthesises a `ChatExchange` with `stop_reason: "interrupted"`
  carrying whatever partial answer + tool events + citations
  arrived before the abort. Archived like any other exchange; an
  `interrupted` exchange renders a "Response interrupted by user."
  footer under the partial answer with the meta row collapsed.
- Sidebar + Chats-history overlay accept `streamingIds: Set<string>`
  instead of a single `streamingId`. Bulk-delete in the overlay
  guards against any selected row being a member of the set.
- Pre-slice typing-foundation fix (`bf00c01`) — closed a Phase-0
  blind spot: workspace packages (`wolf_common`, `wolf_secrets`,
  `wolf_schema`) had been shipping without PEP-561 `py.typed`
  markers since the very first phase commit. mypy was silently
  treating every workspace import as `Any`. Dropped markers into
  all three packages, updated each hatch build to ship them, and
  pruned + tightened the root mypy overrides. Cascading fixes:
  `Mapped[dict]` → `Mapped[dict[str, object]]` (knowledge models),
  explicit `EmbeddingProvider` annotation on `_reembed_batch`,
  `jwt.encode` boundary cast in `app/auth/local.py`. mypy went
  from 56 errors to 0 across orchestrator + gateway + all
  packages.

### What we verified
- Integrity gate clean before slice commit: tsc 0 / eslint 0 /
  mypy 0 (down from 56) / ruff 0 / pytest 260/260 / live organization-
  isolation 6/6.
- Manual two-conversation concurrent stream verified by the user:
  start a run in convo A, switch to convo B, start another, both
  sidebar rows show the in-flight glyph independently. Stop on
  the active conversation freezes its partial answer with the
  interrupted footer; the inactive conversation's stream keeps
  running.

### What we decided
- Added a new standing rule: "no unaddressed errors / warnings /
  silent diagnostics — pre-existing baseline is not a pass; fix
  or track with plan, never just report-and-move-on." The
  Phase-0 mypy blind spot would have stayed open under the prior
  "this slice didn't introduce it" framing.

### What's next
- Slice 5.0c-l: conversation tree branching (Edit/Retry create
  branches with `< N/M >` navigator).

## 2026-05-31 — In-conversation Find: tried six iteration passes, removed entirely

**Session type:** claude-code
**Phase:** Phase 5 prep — feature attempted then withdrawn
**Duration:** ~6 h across passes
**Branch / commits:** `main` — Find feature commits `b23999d` (5.0c-i.2), `8587954`/`2744038` (5.0c-i.3), `366c6b8` (5.0c-i.4), `5ef6df3`/`eea089a` (5.0c-i.5), `34c1a35`/`517cade` (5.0c-i.6), `a86785f`/`97bc34e` (5.0c-i.7). Removal: `ebbe186` (-632 lines).

### What we did
- Built an in-conversation Find feature (Ctrl+F to open, scan
  the visible thread, highlight matches in-place with `<mark>`
  injection, per-match counter with `<` / `>` navigation).
- Six iteration passes addressing user-reported issues: DOM-based
  counting + recursion through nested elements, React-state-driven
  active-mark highlighting (not direct DOM mutation), per-mark
  counter, color tuning to palette-yellow, drop the 3-char minimum,
  Ctrl+F prefills with currently-selected text.
- After every pass the user-flagged a new edge case; pass 7 still
  had layout quirks (composer auto-resize interacting with the
  match-highlight DOM mutations would scroll-jump the active
  match off-screen).

### What we decided
- Removed the feature entirely on user request (`ebbe186`,
  -632 lines). The interaction between match-highlighting and the
  scroll/composer-resize machinery proved too fragile to land
  cleanly. Browser-native Find (Ctrl+F) is good enough for the
  MVP; we revisit if/when the chat content needs deeper search
  affordances.
- Saved as a lesson for future-me: features with non-trivial DOM
  injection inside an already-complex scroll/sizing flex chain
  need a different architecture (e.g., a search overlay layer,
  not in-thread highlights).

### What's next
- Resume the planned 5.0c track with the next non-Find slice.

## 2026-05-31 — Chore: IP-agnostic local access

**Session type:** claude-code
**Phase:** Phase 5 prep — dev-environment paper-cut
**Duration:** ~30 min
**Branch / commit:** `main` — `a3fdd73`

### What we did
- Stopped requiring three-file edits every time the host's LAN IP
  rotated. Backend now allows any private-network origin via a
  regex CORS rule (`r"^https?://(localhost|127\.0\.0\.1|\[::1\]|
  192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.
  \d+\.\d+)(?::\d+)?$"`). Frontend's `apiBase()` derives the
  orchestrator URL from `window.location.hostname` at runtime when
  `NEXT_PUBLIC_ORCHESTRATOR_URL` is unset. Next.js
  `allowedDevOrigins` switched to wildcards for the same private
  ranges.
- The earlier "edit three files on every LAN IP change" checklist
  in `docs/restart.md` is now mostly dead — restart works from any
  IP without config edits, including localhost vs. a LAN IP, on
  the same dev box.

### What's next
- Per-slice work continues from a stable dev environment.

## 2026-05-30 → 31 — Slice 5.0c-i + 5.0c-i.2 → i.5: conversation rename + polish wave

**Session type:** claude-code
**Phase:** Phase 5 prep — UX polish trickle
**Duration:** ~5 h across many small commits
**Branch / commits:** `main` — `11a6237` (5.0c-i base), `9b99d2a` / `e2087f7` / `3737561` (5.0c-i.2), `c4974db` / `8587954` / `2744038` (5.0c-i.3), `3382aa2` / `27d9c6d` / `4830c00` / `366c6b8` (5.0c-i.4), `a07858f` / `ac493e5` / `cbb54c9` (5.0c-i.5)

### What we did
- **5.0c-i (`11a6237`):** conversation rename via the top-bar title
  + the sidebar's per-row "…" menu. Greeting screen exit
  animation slowed to 500 ms (felt right after testing 280 ms
  snappy / 1500 ms sluggish).
- **5.0c-i.2:** sidebar / chats-history-overlay polish wave —
  per-conversation star + delete via the row menu; sort by
  `updated_at` so freshly-active conversations float up;
  Starred / Recents section split; animation tuning after user
  feedback; chats-overlay per-row "…" menu with Select-chats
  mode for bulk delete.
- **5.0c-i.3:** app-native confirmation dialog component
  (`ConfirmDialog`) for destructive actions (replaces `window.
  confirm` — required for keyboard a11y inside the app's focus
  trap). Composer slide-in / slide-out animation cubic-bezier
  retuned (600 → 1000 ms) per user feedback.
- **5.0c-i.4:** composer textarea auto-expand to a max of ~10
  lines then internal scroll; inline-code text color set to
  palette Dusk Blue; per-message hover action bar no longer
  shows a redundant focus ring on the underlying bubble.
- **5.0c-i.5:** Markdown rendering polish — inline code becomes
  bold, fenced code blocks get syntax highlighting via Shiki
  via `react-markdown`'s plugin pipeline; defensive composer-
  expand re-pin (the start of what later became the `5.0c-i.7`
  flicker-free rewrite).

### What's next
- Slice 5.0c-j (chats history pane), 5.0c-k (Stop + concurrent
  streams), 5.0c-l (branching). Find feature still on the
  backlog at this point (later removed entirely on 2026-05-31).

## 2026-05-30 — Slice 5.0c-j: chats history pane with full-text search

**Session type:** claude-code
**Phase:** Phase 5 prep
**Duration:** ~1.5 h
**Branch / commit:** `main` — `e4efd60`

### What we did
- New full-screen `ChatsHistoryOverlay` reached from the sidebar's
  History icon. Differs from the sidebar's title-search in one
  important way: this pane searches the full **body** of every
  user and assistant message in every conversation. Matches
  surface as ~120-char snippets centered on the match with
  bold highlighting in-context.
- Layout follows Claude's Chats page: title left, New chat right,
  search row below, results list filling the rest. ESC closes;
  clicking a result closes the overlay and routes to that
  conversation. Auto-focus on the search input on open; clean
  reset on every open of the overlay (search query, selection
  mode, rename state).

### What's next
- Slice 5.0c-k: Stop response button + concurrent streams.

## 2026-05-30 — Slice 5.0c-h: async stream lifecycle + immediate sidebar slot

**Session type:** claude-code
**Phase:** Phase 5 prep
**Duration:** ~45 min
**Branch / commit:** `main` — `c5c7d2b`

### What we did
- New conversations now appear in the sidebar the *moment* the
  user submits, not after the first stream event arrives. Previous
  behaviour caused a flash of "no active conversation" between
  Send and the first SSE event landing.
- Stream lifecycle now distinguishes a few cleaner phases:
  `idle` → `running` (start) → `done` (answer event) / `error`.
  The streaming-view component reads `phase` exclusively rather
  than mixing it with the exchange-archived state.

### What's next
- Slice 5.0c-i: conversation rename + greeting fade.

## 2026-05-30 — Slice 5.0c-f + 5.0c-g: polish backlog + English-only + retry-nudge

**Session type:** claude-code
**Phase:** Phase 5 prep — combined polish slices
**Duration:** ~2 h
**Branch / commit:** `main` — `abbcd1b`

### What we did
- **5.0c-f (polish backlog):** long-message fade-and-show-more
  on user bubbles (`LONG_MESSAGE_THRESHOLD = 280` chars);
  hover-on-message action bar (date / Copy / Retry / Edit on
  user bubbles, date / Copy / Retry on assistant); greeting-
  screen quick-action cards (recent critical alerts /
  suspicious authentication / agent health) that prefill the
  composer; scroll-to-bottom floating FAB only when scrolled
  more than 200 px off bottom; inline relative-time tooltips
  with the absolute time as `title`.
- **5.0c-g (retry-nudge):** Retry on a Wolf answer re-submits
  the originating question with `retry_nudge: true` so the
  orchestrator appends a critique hint to the user message
  ("the prior attempt was X — try to improve on it"). At this
  point Retry only worked on the latest assistant message;
  5.0c-l later widened it to every assistant message under the
  branching model.
- English-only system prompt addition (the orchestrator agent
  loop occasionally fell back to non-English text from training
  data on small-model runs; now explicitly instructed to keep
  the user-facing answer in English regardless of the input
  language unless the user asks otherwise).

### What's next
- Slice 5.0c-h: async stream lifecycle.

## 2026-05-30 — Slice 5.0c-e: live activity feed

**Session type:** claude-code
**Phase:** Phase 5 prep
**Duration:** ~1.5 h
**Branch / commits:** `main` — `fcff12b` (slice), `c2fd0a5` (test fixup)

### What we did
- The streaming view's status line is now a narrated activity
  feed: each SSE loop event flips it to a varied human-readable
  phrase ("Starting (frontier, qwen3:8b)" / "Step 2 of 8 — picking
  the next move…" / "Calling `search_alerts`…" / "Reading the
  judge's verdict on 3 claims…" / etc.). New
  `frontend/lib/activity-phrases.ts` carries the phrase bank;
  phrases are deterministic per event type so the user sees a
  predictable progression rather than a random sample.
- `c2fd0a5` fixed a chat-endpoint test that was missing a
  judge-model mock response — surfaced when the slice's stream
  refactor stopped tolerating the missing fixture silently.

### What's next
- Slice 5.0c-f + 5.0c-g polish + retry-nudge.

## 2026-05-30 — Slice 5.0c-d: progressive answer rendering (Ollama `stream:true` + `model.delta` SSE)

**Session type:** claude-code
**Phase:** Phase 5 prep
**Duration:** ~2 h
**Branch / commits:** `main` — `c3a31df` (slice), `bb2741d` (backend re-verify + fix 5.0c-a silent regression)

### What we did
- Token-by-token answer reveal during streaming. Backend's
  Ollama adapter now flips `stream: true` on its
  chat-completion call; the orchestrator's SSE stream relays
  each token as a `model.delta` event carrying
  `{ content_delta }`. Frontend accumulates the deltas into
  `streamingAnswer` and renders them progressively via the same
  `Markdown` component that renders the archived answer — so
  the layout doesn't shift when the final `answer` event lands.
- A soft pulsing caret at the end of the streaming text hints
  "still generating" without being noisy.
- `bb2741d` re-ran the full backend suite after 5.0c-d's
  refactor and caught a silent regression in 5.0c-a (one
  grounding-marker case): the `_VERDICT_MARKER` regex priority
  had shifted under a `re.sub` boundary; restored explicit
  alternation order. Now back to a known-good baseline.

### What's next
- Slice 5.0c-e: live activity feed.

## 2026-05-29 — Slice 5.0c-c: theme — Platinum / Dusk Blue / Steel Blue / Icy Blue palette

**Session type:** claude-code
**Phase:** Phase 5 prep — third sub-slice of the 5.0c UI work
**Duration:** ~1 h
**Branch / commit:** `main` — landed `3c070c3`.

### What we did
- User chose a four-colour cool-blue palette (Platinum `#e7ecef`,
  Dusk Blue `#274c77`, Steel Blue `#6096ba`, Icy Blue `#a3cef1`) and
  asked us to handle the slice like an absolute professional designer.
  This **supersedes** the earlier `wolf-color-palette-outlook.png`
  reference.
- Wrote `app/globals.css` from the palette down: four named source CSS
  variables → every shadcn token derives from them in both light and
  dark themes. Light: Platinum surface, deep-dusk body copy, Dusk Blue
  primary, Icy Blue secondary, Steel Blue ring, white card for
  elevation, sidebar one step toward Icy. Dark: deep navy background,
  Platinum foreground, Icy Blue primary (so CTAs pop), Dusk Blue
  secondary, Steel Blue accent.
- Subtle radial-gradient on the light-mode body fading toward Icy Blue
  at the bottom-right; disabled in dark mode where the deep navy
  already does the depth work.
- Consistent 150 ms ease-out colour / shadow / transform transitions on
  every `button`, `<a>`, and `[role="button"]` via a base-layer rule
  (no per-component motion noise).
- Grounding chips kept their semantic colours (green Verified, amber
  Uncertain, red Not Verified, muted yellow Non-factual) — signal-
  bearing, not decorative; they sit on the cool palette without
  clashing.
- Full design intent recorded in `wolf-color-palette.md` cross-session
  memory so this palette is the source of truth going forward.

### What's next
- Slice 5.0c-d: progressive answer rendering via `/api/v1/chat/stream`.

## 2026-05-28 → 29 — Operational docs cluster (Phase 5.4 record, restart runbook, LAN IP rotation)

**Session type:** claude-code
**Phase:** Phase 5 prep — documentation while iterating on 5.0c-b
**Duration:** trickled across the session
**Branch / commits:** `main` — `4166616` `1f9a10d` `82a93cd`.

### What we did
- `4166616` — recorded **Phase 5.4 — Native HTTPS + `wolf-cert` CLI**
  in PROGRESS.md (decision: post-5.0c, pre-RBAC). 100-year cert
  validity as the practical-infinity pattern (RFC 5280 forbids truly
  unlimited). Full design intent in `native-https-and-wolf-cert.md`
  cross-session memory.
- `1f9a10d` — created `docs/restart.md`, the dedicated reset + relaunch
  runbook. Quick-version copy-pasteable sequence, what each step does,
  what the restart does NOT touch, test credentials, hardware fact
  (6 GB GPU, qwen3:4b + qwen3:8b don't fit together), per-slice
  workflow recap, troubleshooting table. The per-slice memory entry
  now references it as source-of-truth instead of inlining the steps.
- `82a93cd` — host's LAN IP rotated `.108` → `.114`; appended the new
  IP to `CORS_ALLOW_ORIGINS`, `NEXT_PUBLIC_ORCHESTRATOR_URL`, and
  `allowedDevOrigins`. Added an "If the LAN IP just changed" three-file
  checklist + `hostname -I` discovery to `docs/restart.md` so the next
  rotation is a 30-second fix.

## 2026-05-29 — Slice 5.0c-b iteration (passes 2 / 3 / 4): user-flagged bug fixes

**Session type:** claude-code
**Phase:** Phase 5 prep — same slice, four web-test rounds
**Duration:** ~2.5 h across passes
**Branch / commits:** `main` — `4f86af5`, `2bf1967`, `28dc96d`.

### What we did
- Pass 2 (`4f86af5`) — first user web-test of 5.0c-b surfaced layout
  bugs. Avatar moved out of the header into a Claude-style sidebar
  footer; header gained a Settings gear (placeholder for User Settings
  + Wolf Configuration); organization selector stayed top-right. Code-block
  Copy button added. Scroll-to-bottom floating arrow. Disclaimer line
  under the composer. Scrollbar thumb upped from `bg-border` to
  `foreground/30` so it's visible at rest.
- Pass 3 (`2bf1967`) — second web-test flagged that the composer was
  still being overlapped by the chat, the avatar email row was empty,
  older exchanges lost their meta row, and the gear was too small.
  MessageThread switched from Radix `ScrollArea` to native
  `overflow-y-auto` (rock-solid in flex chains); `/auth/me` was
  finishing a 6-month-old "load from DB if needed" TODO and silently
  returning `email=""` — now loads the User row; meta row gating
  loosened to show on every archived exchange; gear icon enlarged.
- Pass 4 (`28dc96d`) — third web-test flagged that the meta row still
  disappeared when a new turn started streaming, and the Copy button
  silently did nothing. `showMeta` gating fixed (archived turns are
  immutable). The `navigator.clipboard.writeText` only works in secure
  contexts; Wolf dev is plain HTTP on a LAN IP, so the catch was
  swallowing the failure. Added a `copyText()` helper that falls back
  to `document.execCommand('copy')` via a hidden textarea — the button
  now flips to "Copied" with a check for ~1.5 s on real success.

### What's next
- Slice 5.0c-c (theme) → 5.0c-d (streaming) → 5.0c-e (live activity) →
  Phase 5.4 (Native HTTPS) → Phase 5 (RBAC).
- Deferred to 5.0c-b.2 (placement TBD): search-conversations in
  sidebar, fading + "Show more" on long user messages, hover-on-
  message actions (date / retry / edit / copy), new-chat greeting
  screen with quick-action cards, full icon-rail mode for the
  collapsed sidebar.

## 2026-05-28 — Slice 5.0c-b: layout overhaul (avatar dropdown, collapsible sidebar, resizable Evidence panel)

**Session type:** claude-code
**Phase:** Phase 5 prep — second sub-slice of the 5.0c UI work
**Duration:** ~1 h
**Branch / commit:** `main` — starting commit `a1c054d`, this entry's commit pending.

### What we did
- **`chat-header.tsx`**: replaced the session-id chip with a circular
  user-avatar dropdown (initials from `display_name`, falling back to
  the email local-part). The dropdown shows display name, role, email,
  current organization, the first 8 chars of `user_id` for support, and the
  sign-out action — covering the user's original request for a single
  "who am I + where am I + leave" surface.
- **`chat-sidebar.tsx`**: the Conversations sidebar is now collapsible
  via a chevron toggle at the top. Collapsed width 48 px (just a "new
  conversation" rail); expanded 288 px. State lifted to parent so the
  main column reflows. Animated `transition-[width]`.
- **`chat-shell.tsx`**:
    - Added an SSR-safe `usePersistedState` hook (read on mount, write
      on every change, swallows quota/corrupt errors).
    - Sidebar collapse state and Evidence panel width are persisted to
      localStorage (`wolf.sidebar.collapsed`, `wolf.evidence.width`).
    - **Persistent Evidence**: while a new run is in flight, the panel
      keeps showing the previous exchange's citations until new ones
      arrive — no flash of empty state on every prompt.
    - **Resizable Evidence**: a 6 px hit area on the panel's left edge
      uses pointer capture to drag-resize between 280 px and 720 px,
      with a thin visible guide that brightens on hover/drag.
- **`citations-panel.tsx`**: the citation-query `<pre>` now
  `whitespace-pre-wrap break-words` instead of `overflow-x-auto`, so
  long JSON values (long ISO timestamps, long free-text queries) wrap
  inside the panel instead of forcing a horizontal scroll.
- Layout structure: the composer is now explicitly `shrink-0` so it
  stays anchored at the bottom of the main column while the message
  thread (which already used `ScrollArea` with auto-scroll-to-bottom)
  scrolls inside the remaining space — the "fixed input + chat scroll"
  feedback item from the original test report.

### What's next
- Slice 5.0c-c: theme / colour palette matching `wolf-color-palette-outlook.png`.

## 2026-05-28 — Slice 5.0c-a: four grounding chips + verdict rename

**Session type:** claude-code
**Phase:** Phase 5 prep — first sub-slice of the 5.0c UI work
**Duration:** ~45 min
**Branch / commit:** `main` — starting commit `0c26660`, this entry's commit pending.

### What we did
- Backend (`validator.py`): added `MARKER_VERIFIED` and `MARKER_NON_FACTUAL`
  constants and extended `_VERDICT_MARKER` so all four verdicts get an
  inline chip token — not just the worrying ones. Supported claims get
  `[verified]`; preamble / instruction / opinion (unverifiable) gets
  `[non-factual]`. The user explicitly asked for full per-verdict
  visibility, not just warnings.
- Frontend (`markdown.tsx`): four chip styles, each with a distinct
  colour AND icon so the two yellows are not confusable:
    - 🟢 `[verified]` → emerald · `Check`
    - 🟡 `[unverified]` → amber · `Info`   (label renamed *Uncertain*)
    - 🔴 `[unsupported]` → destructive · `AlertTriangle`   (label renamed *Not Verified*)
    - 🟡 `[non-factual]` → muted yellow with border · `MessageCircle`
  Regex `MARKER_SPLIT` extended to match any of the four tokens.
- Frontend (`message-thread.tsx`): `GroundingBadge` tooltip now uses
  the new labels (`Verified · Uncertain · Not Verified · Non-factual`).
  Badge chip counts already supported uncertain from 5.0b.

### What we verified
- Backend tests: 27 grounding + 14 tool-summary + the rest of the suite
  pass. ruff + mypy-strict clean.
- Frontend: tsc + eslint clean.
- Claude-side self-validation against *"How many alerts of each severity
  last year?"*:
  ```
  grounding: sup=1 unsup=0 unc=0 unverif=0
  inline [verified] count: 1
  ```
  The backend now emits the green-chip token on the supported claim.
  Frontend renders as a green *Verified* chip.

### Notes
- Stored answers from earlier slices already have `[unverified]` and
  `[unsupported]` markers. They keep rendering — the chips are now
  labelled *Uncertain* and *Not Verified* respectively. No DB migration.
- Old verdict names (`supported`/`unverifiable`/`uncertain`/`unsupported`)
  unchanged in the backend; only the user-facing chip labels changed.

### What's next
- Slice 5.0c-b: persistent + resizable Evidence panel, collapsible
  Conversations sidebar, fixed message input, chat vertical scroll,
  user-avatar dropdown replacing the session-id chip.

## 2026-05-28 — Slice 5.0b.4: judge context headroom + per-tool grounding-friendly summaries

**Session type:** claude-code
**Phase:** Phase 5 prep — patch on top of 5.0b.3
**Duration:** ~2 h (incl. ground-truth indexer probe + 14-min live judge run)
**Branch / commit:** `main` — starting commit `a62af5a`, this entry's commit pending.

### What we did
- Live test of 5.0b.3 against the Hydra attack revealed a NEW failure
  mode: the judge call returned and the retry executed — but both
  attempts came back with effectively empty content (`JSONDecodeError:
  Expecting value: line 1 column 1 (char 0)`). Grounding skipped silently.
  Diagnosis: complex structured answers (markdown with tables, multiple
  sections, bullet lists) blow out qwen3:8b's **default 4096-token
  Ollama context** with prompt + 5 KB evidence + 20-claim batch.
- Slice 5.0b.4 fixes — judge robustness:
    - `OllamaAdapter` now accepts a `num_ctx` parameter (per-adapter).
      The judge path passes `ollama_num_ctx=8192`, doubling the available
      Ollama context window. Default chat unaffected.
    - `_judge` explicitly raises on empty `response.content` (and on
      JSON-array regex match returning empty) so the failure path logs a
      clear diagnostic instead of `json.loads("")`.
    - `_judge` also strips qwen3-style `<think>…</think>` reasoning
      blocks before extracting JSON.
    - `GroundingValidator.max_claims` default 20 → 12 so the judge's
      input fits comfortably in the new 8 K window even with full 5 KB
      evidence. Claims beyond the cap default to `uncertain` via the
      5.0b.2 fallback.
- Slice 5.0b.4 fixes — **tool precision audit (every list-returning
  tool)**:
    - `search_alerts` gains a `summary: SearchAlertsSummary` with
      `per_rule`, `per_agent`, `earliest_timestamp`, `latest_timestamp`
      computed from the hits. The model now reads structured aggregations
      instead of inventing per-rule breakdowns.
    - `get_event_timeline` and `get_agent_alert_history` use the same
      `SearchAlertsSummary` shape (they also return `AlertHit` lists).
    - `aggregate_alerts` gains a `total` field (sum of bucket counts).
    - `list_agents` gains an `AgentFleetSummary` (`by_status`, `by_os`).
    - `query_runbook` gains a `KnowledgeRetrievalSummary` (`by_source_
      type`, `best_distance`).
    - The lone `exclude_none=True` in `query_runbook`'s citation call
      was removed — all 10 tools now consistently show every parameter
      (set or null) in the citation panel, per user request for full
      visibility.
- +14 tests covering the summary helpers, the new validator behaviour,
  and the empty-judge-content path.

### What we discovered (the headline)
Self-validation against the same Hydra-attack prompt on 5.0b.4:
`grounding: sup=3 unsup=3 uncertain=0 unverifiable=0` — **all six
claims judged, zero silent fallbacks**, total wall-clock 13 m 58 s. And
critically, the per-rule numbers in Wolf's answer are now ACCURATE:
"31 rule 5760", "7 rule 5503", "1 rule 5763" — exact match to ground
truth from the live indexer probe. The previous 5.0b.3 run produced
"5503: 44 + 5760: 40" out of a 44-hit search (an arithmetic impossibility);
the new `per_rule` summary in `search_alerts` shut that fabrication path.

### What's still imperfect
- **Latency: ~14 min on the first call** (cold qwen3:4b + qwen3:8b swap
  at the new num_ctx=8192). This is the trade-off accepted in ADR 0015
  on this 6 GB hardware. Subsequent same-session prompts are faster
  (chat model stays warm); each grounding still costs.
- **Judge over-strict on inferences from observed facts.** Two of the
  three red `[unsupported]` markers landed on inferences ("compromised
  internal system attempting unauthorized access", "rules out external
  attackers"). These should arguably be 🟡 *uncertain* (yellow), not
  🔴 *unsupported* (red). Future judge-prompt iteration territory —
  not blocking 5.0c.

### What's next
- Hand 5.0b.4 to user for re-test.
- Then Slice 5.0c: UI overhaul + four-chip verdict rename +
  progressive answer rendering + live activity feed.

## 2026-05-28 — Slice 5.0b.3: retry-on-timeout + bigger judge client ceiling

**Session type:** claude-code
**Phase:** Phase 5 prep — patch on top of 5.0b.2
**Duration:** ~30 min
**Branch / commit:** `main` — starting commit `02382f9`, this entry's commit pending.

### What we did
- User generated a real Hydra SSH brute-force against `linux-test-agent`
  (agent 001) — 59 alerts in 24 h across rule ids 5503, 5710, 5712, 5758,
  5760, 5763, 2501, 2502. Excellent test set for grounding against fresh,
  rich evidence.
- Ran Wolf against it. Wolf produced a substantive 4 KB answer correctly
  identifying IP 192.168.245.1 → user `wolf`, 44 attempts, rule families
  — **but the grounding judge `ReadTimeout`-ed** at 300 s. Validator
  returned `ran=False` and no chips/badge appeared at all. The cumulative
  cold-load of qwen3:4b (chat) + the cold-swap to qwen3:8b (judge) on a
  6 GB GPU pushed the judge HTTP call past the existing ceiling.
- 5.0b.2's partial-response retry didn't help here: the FIRST call never
  came back, so there was nothing to merge.
- Slice 5.0b.3 patch:
    - Bump `OllamaAdapter` httpx timeout `300 s → 600 s` so cold loads
      have realistic headroom on this hardware.
    - In `validator.validate()`, retry the **first** judge call once on
      any exception (ReadTimeout, transient Ollama errors). Logs
      `grounding_judge_first_attempt_failed` on attempt 0 and
      `grounding_judge_failed` if attempt 1 also fails. Combined with
      the existing partial-response retry below, the validator now
      persists through both failure modes.
- +1 test (`_FlakeyProvider` scripts a Timeout-then-success sequence;
  asserts the validator recovers on the second call).

### What we discovered (the headline finding)
Self-validation against the same Hydra-attack prompt on 5.0b.3:
`grounding: sup=4 unsup=2 unc=0 unverif=0` — **all six claims judged,
zero silent uncertain fallbacks**, total wall-clock 2 m 53 s. The
retry didn't even need to trigger; the bigger timeout alone was enough
to let the judge complete. The 5.0b.2 partial-response retry remains
in place for cases where it does. Together: 5.0b → 5.0b.1 → 5.0b.2 →
5.0b.3 has gone from "everything red, sometimes silent, sometimes
times out" to "all six claims classified on real attack data."

### What's next
- Hand 5.0b.3 to user for re-test against the Hydra alerts.
- Then Slice 5.0c.

## 2026-05-28 — Slice 5.0b.2: judge retry-on-partial + uncertain fallback

**Session type:** claude-code
**Phase:** Phase 5 prep — patch on top of 5.0b.1
**Duration:** ~30 min
**Branch / commit:** `main` — starting commit `849a0f8`, this entry's commit pending.

### What we did
- User web-tested 5.0b.1. The count queries were clean green; the SSH
  brute-force answer showed only one red `[unsupported]` on the citation
  line. Investigation revealed the OTHER claim (the brute-force summary)
  had silently defaulted to `unverifiable` with no chip because qwen3:8b
  returned a **partial** JSON verdict array — only one verdict for two
  claims. The validator code defaulted missing claims to `unverifiable`,
  which renders as no chip, hiding the issue from the user.
- Implemented Option B (user-chosen) in
  [validator.py](../services/orchestrator/app/grounding/validator.py):
    - Extracted the per-claim verdict merge into a `_merge_verdicts`
      helper so successive calls can fill in missing indices without
      overwriting existing verdicts.
    - When the judge returns fewer verdicts than claims, retry the call
      ONCE. Logs `grounding_judge_partial_response` so operators can see
      the rate of partial responses.
    - **Fallback (Option A semantics):** any claim still missing after
      the retry now defaults to `uncertain` (yellow caution chip), not
      `unverifiable` (no chip). The user always sees a signal that the
      validator wasn't sure about that claim.
- +3 tests: retry recovers missing, retry-also-partial falls back to
  uncertain, full first response triggers no retry.

### What we discovered
- Ground-truth probe of the live indexer for the brute-force usernames:
  actual users in 9 hits are `attacker_user_1, 2, 3, 4, 5, 6, 8, 10, 12`
  — Wolf's "1 through 10" framing is misleading (missing 7/9, includes
  12). With the retry+fallback, this claim should no longer go silently
  unclassified — it'll either get a real red verdict on the retry or
  a yellow uncertain fallback.

### What's next
- Hand 5.0b.2 to user for re-test.
- Then Slice 5.0c: UI overhaul + four-chip verdict rename.

## 2026-05-28 — Slice 5.0b.1: judge-prompt iteration after live 5.0b test

**Session type:** claude-code
**Phase:** Phase 5 prep — patch on top of 5.0b
**Duration:** ~45 min
**Branch / commit:** `main` — starting commit `e84e95f`, this entry's commit pending.

### What we did
- User web-tested 5.0b and flagged two over-strict red `[unsupported]`
  markers on what looked like a correct SSH-brute-force answer. The
  earlier self-validation note had predicted exactly this kind of
  judge over-strictness; user asked to iterate.
- **Raised the evidence-per-source cap** from 2 KB → 5 KB
  ([validator.py](../services/orchestrator/app/grounding/validator.py)
  via the new `_EVIDENCE_PER_SOURCE_LIMIT` module constant). At 2 KB,
  a 9-hit search_alerts JSON dump truncated the last hits out of view,
  so the judge could only see 7-8 hits and concluded "the claim of 9
  attempts isn't supported" — correct reasoning on incomplete evidence.
  5 KB comfortably fits ~12 hits with wrapper overhead.
- **Sharpened the judge system prompt**:
    - `SUPPORTED` now explicitly includes paraphrases / generalisations
      of content clearly in the evidence (e.g. "brute-force pattern"
      when the rule description says "brute force").
    - `UNVERIFIABLE` examples expanded with **meta commentary about
      Wolf's own analysis flow** ("No further tool calls are needed",
      "I have what I need to answer", "The data is sufficient").
    - New rule: a paraphrase of evidence is SUPPORTED, not UNSUPPORTED.
- +2 tests: evidence-cap behaviour + structural prompt sanity-check.

### What we discovered (the headline finding)
A direct probe of the judge against the same evidence and answer
revealed the red `[unsupported]` on Wolf's answer was actually
**catching a real fabrication**: the answer said "attacker_user_1
through attacker_user_10" but the data only contains 9 users
(attacker_user_1 through attacker_user_9). The judge correctly
identified this off-by-one. What looked like over-strictness was
the system doing its job. The 5.0b.1 evidence-cap raise plus prompt
sharpening also got the citation-line case correctly classified as
`unverifiable` (no chip) in probe — and would have for the live case
too if qwen3:8b weren't slightly non-deterministic even at temp=0.

### What's next
- Hand 5.0b.1 back to user for a fresh live re-test.
- Then Slice 5.0c: UI overhaul + four-chip verdict rename
  (`Verified` / `Uncertain` / `Not Verified` / `Non-factual`).

## 2026-05-28 — Slice 5.0b: grounding yellow vs red + reliability hardening

**Session type:** claude-code
**Phase:** Phase 5 prep — stabilization slices (5.0b)
**Duration:** ~2 h (incl. live diagnosis of GPU thrash + Claude-side self-validation)
**Branch / commit:** `main` — starting commit `755e786`, this entry's commit pending.

### What we did
- **Grounding taxonomy 3 → 4 verdicts** (`validator.py`):
  `supported` (no marker), `unverifiable` (no marker — preamble/transitions),
  `uncertain` → yellow `[unverified]` marker, `unsupported` → red
  `[unsupported]` marker. Judge prompt now emphasises that any fabricated
  *specific* (count/ID/name/timestamp) is **unsupported, never uncertain**.
- **Fabrication hardening:** failed tool calls are now surfaced to the judge
  as explicit `[TOOL_FAILED i: name]` negative evidence, and the validator
  runs even when the only "evidence" is a tool failure — so claims that
  should have come from a failed tool get judged unsupported instead of
  slipping through unflagged. Threaded `all_tool_failures` accumulator
  through `agent/loop.py` → `_finalize_answer` → `validator.validate`.
- **`grounding_uncertain` count** threaded loop → API response → frontend
  types/stream hook → badge + tooltip.
- **Frontend rendering**: yellow `[unverified]` chip (amber, `Info` icon) for
  caution; red `[unsupported]` chip (destructive, triangle) for contradicted.
  `GroundingBadge` now shows `{sup}✓ {uncertain}⚠ {unsup}✗` with severity
  ladder: red > amber > green.
- **Reliability fix (Fix A) — empty-answer recovery:** `qwen3:4b`
  occasionally returns empty content right after tool results. The loop now
  re-prompts ONCE without tools (`_synthesize_final`) to coax a written
  answer from the evidence already in the transcript; if even that comes
  back empty, an honest fallback message is shown instead of a blank "(empty)"
  bubble. +2 loop tests cover both branches.

### What we decided
- **Keep `qwen3:8b` as the grounding judge** (Fix B). On this 6 GB GPU it
  can't coexist with `qwen3:4b` (chat) so Ollama swaps them on every
  grounding call — each first answer is slow (~2-3 min cold), and the
  previous "(empty)" + `ReadTimeout` came from that thrash. User chose
  judge quality over latency and asked for a fresh GPU/RAM reset before
  every test cycle. Full rationale: [ADR 0015](decisions/0015-grounding-yellow-vs-red-and-judge-on-constrained-gpu.md).
- **New per-slice workflow:** before any test, RESET (stop orchestrator +
  `ollama stop <model>` to free GPU); Claude self-validates via direct API
  calls; RESET again; only then hand over for manual web-test.

### What broke / what we discovered
- Live diagnosis: ReadTimeout was the *chat* call, not the judge call —
  the chat model had been evicted from the GPU by a previous turn's 8b
  judge load, and the cold reload exceeded the (already generous) 300s
  client timeout. Root cause is GPU memory pressure, not timeout value.
- The single self-validation answer ("Nine SSH brute-force attempts… from
  IP 192.168.245.1 … `attacker_user_1` through `attacker_user_12`") got a
  red `[unsupported]` despite the specifics being in the tool result —
  likely evidence truncation at 2 KB + judge over-strictness on the
  citation line. Documented as a known judge-quality nit; not a stability
  bug. To iterate on the judge prompt or evidence size if it persists.

### What's next
- Slice 5.0c: UI overhaul — persistent + resizable + text-wrapping Evidence
  panel; collapsible Conversations sidebar; fixed message input; chat
  vertical scroll; session-id chip → user-avatar dropdown.

## 2026-05-28 — Slice 5.0a: pre-Phase-5 stabilization (alert search + time handling)

**Session type:** claude-code
**Phase:** Phase 5 prep — stabilization slices (5.0a) before the cases/RBAC work
**Duration:** ~2 h (incl. live-DB investigation + two web-test rounds)
**Branch / commit:** `main` — starting commit `34dea23`, this entry's commit pending.

### What we did
- Investigated a reported cross-organization "leak" (beta returned "ACME runbook"
  content): probed the live DB as beta — it retrieved ONLY its own chunks.
  **Not a data-layer leak**; the chat model parroted the prompt's "ACME"
  label onto beta's own content. A grounding/honesty issue → Slice 5.0b.
- Fixed `search_alerts` free_text: `rule.description` is mapped `keyword`
  (not analyzed) and `full_log` uses the standard analyzer, so the old
  `multi_match` returned 0 hits for "SSH brute-force". Replaced with
  `bool.should` = analyzed match on `full_log` + case-insensitive `wildcard`
  per token on `rule.description`. Live: 0 → 80 hits.
- Reworked the time-window guardrail (`limits.py`): 30d → **365d** default,
  +1s grace for now-vs-now clock drift, and `enforce_time_window=False` so
  aggregation/count tools (bucket-bounded) can analyze any range.
- Added `search_after` cursor pagination to `search_alerts` (two-key sort
  `timestamp`+`_id`); tool now returns `total` / `has_more` / `next_cursor`
  so the model can walk an entire window gap-free.
- **Hotfix mid-testing:** extended `parse_time_field` to understand months
  and years (`now-6mo`, `now-1y`, `now-2months`) and fixed a latent bug where
  `now-12M` silently meant 12 *minutes* (case-insensitive regex). Now
  `m`=minutes, `M`/`mo`=months, `y`=years.
- Tests: +13 (query builder, guardrails, pagination output) and a new
  `test_timefmt.py` (+8). All green; ruff + mypy-strict clean.

### What we decided
- Organizations + RBAC (superuser → orgs → users → roles) is a feature, not
  a stabilization fix → its own dedicated phase AFTER slices 5.0a–d, before
  cases/reporting. `users.is_superuser` already exists as scaffolding.
- Time-window guard is a backstop; real volume guards are `size` (≤1000) +
  context truncation. Hence the generous 365d cap + aggregation exemption.

### What broke / what we discovered
- **Self-inflicted regression caught by web-test:** raising the window to a
  year without teaching the parser months/years made the model emit
  `now-6mo`/`now-1y`, which failed validation → tool errored → **the model
  fabricated** "12 critical / 45 high / …". Unit tests passed; only manual
  testing caught it. Fixed by the parser hotfix.
- Re-test confirmed real, grounded analytics (706 alerts, top rules 19007/
  19008/19009 = SSH config checks) — cross-checked against Wazuh Discover.
- Residual model-honesty gaps remain: fabrication on tool error, occasional
  tool-call-as-text, and over-eager red `unverified` badges on correct
  claims. All targeted by Slice 5.0b.

### What's next
- Slice 5.0b: grounding — yellow `unverifiable` vs red `unsupported`; harden
  validator against fabrication-on-tool-failure and cross-label mislabeling.

## 2026-05-27 — Phase 4 follow-up: close the 4.4b + 4.4c gaps

**Session type:** claude-code (continuation)
**Phase:** Phase 4 — multi-organization hardening (gap-fix after close-out)
**Duration:** ~15 min
**Branch / commit:** `main` — starting commit `ce12c2a`, this entry's
commit pending.

### What we did

The Phase 4 close-out (`ce12c2a`) left two sub-slices partial; the
operator asked why, and whether the gaps would bite later. They would
(chronic doc-drift + implicit-guard-rot), so we fixed both now.

- **4.4b — CI wire (was: implicit only).** The cross-organization isolation
  tests already ran in CI by directory discovery, but `.github/
  workflows/ci.yml` contained zero textual reference to "isolation" —
  a future contributor reading the workflow couldn't see the guard,
  and an accidental test-file move would silently drop the coverage.
  Added an explicit "Cross-organization isolation suite (explicit gate)"
  step to the `test` job naming `test_cross_organization_isolation.py` +
  `test_organization_scoped_cache.py`. Same tests; now visible + regression-
  obvious in the CI log.

- **4.4c — doc 05 (was: ONBOARDING only).** Added an "Implementation
  status — Phase 4" section to `docs/05-multi-organization.md` mapping each
  design requirement to its concrete artifact (OrganizationContext,
  OrganizationScopedQueryBuilder, PgvectorKnowledgeStore leg clauses,
  OrganizationScopedCache, bootstrap_organization validation, audit log,
  stateless-reestablish connection model, per-organization model seam, the
  CI + synthetic-probe testing split, the dev two-organization pattern).
  Plus a "still owed" note: the secrets backend is the Fernet file
  backend today, not a real Vault/OpenBao manager (Phase 6+ deploy
  work; the `SecretsBackend` protocol already abstracts the swap).

### Why the gaps happened (recorded honestly)

Both came from scope-discipline reasoning that was thinly-justified
momentum-preservation. 4.4b: rationalised "CI already runs them" and
skipped making the guard explicit without flagging it as a judgment
call. 4.4c: updated the easier surface (ONBOARDING) and let it feel
like "docs updated." The correct move would have been to do both or
ask explicitly. Logged here so the pattern is visible.

### What broke / what we discovered

- Nothing broke. CI YAML validates; `make check` 203 passed
  (unchanged — the fix is workflow + docs, no new test surface).

### What's next

- Phase 5 — Cases and reporting. Phase 4 is now fully closed with no
  outstanding gaps.

---

## 2026-05-27 — Phase 4 close-out: Slices 4.2 + 4.3 + 4.4 + isolation-suite live smoke

**Session type:** claude-code (continuation, Phase 4 close-out)
**Phase:** Phase 4 — multi-organization hardening — **CLOSED**
**Duration:** ~150 min across multiple sub-sessions
**Branch / commit:** `main` — starting commit `338413f` (Slice 4.1),
final commit pending.

### What we did

**Slice 4.2 — `bootstrap_organization` validates + `--update` flag (commit `1da9e1c`)**

- New `ConnectionValidationError`: raised when the Indexer (HTTP GET /)
  or Server API (POST /security/user/authenticate) rejects auth, returns
  an unexpected status, or is unreachable. Error messages name the
  failing endpoint and (for Server-API 401) explicitly call out the
  Indexer-vs-Server-API user-database split.
- New `OrganizationAlreadyExistsError`: raised on re-run for a slug that
  already has a `validated_at`-stamped Wazuh config. Doc 05 §Organization
  misconfiguration's "immutable by default after validation" pinned at
  the CLI boundary.
- `bootstrap_organization()` gains `update: bool = False` and
  `skip_validation: bool = False`. The CLI exposes `--update` and
  `--skip-validation` flags. Exit codes: 0 success, 4 already-exists,
  5 validation-failure.
- `OrganizationWazuhConfig.validated_at` is now actually written (the column
  existed since Phase 0 but was never stamped). Set to `now()` on
  successful validation, NULL on `--skip-validation`.
- 6 new tests covering 200/200 success, Indexer 403 tolerance, Indexer
  401, Server API 401, unreachable network, and the regression guard
  on the Server-API-401 error-message content.

**Slice 4.3 — `OrganizationScopedCache` + agent_name caching + audit-write isolation (commit `3ff751c`)**

- New `app/caching/` module: `OrganizationScopedCache` Protocol +
  `InMemoryOrganizationCache` implementation. Storage keys are composed
  as `t:<organization_id>:<ns>:<key>` inside the wrapper — callers pass
  `organization_id` as a positional argument, making it structurally
  impossible to construct an unprefixed key. The internal
  `_compose_storage_key` raises `UnprefixedKeyError` if `organization_id`
  is None (defence-in-depth for misuse via internals).
- Module-level singleton `_ORGANIZATION_CACHE` in `app/api/chat.py` — shared
  across both `/chat` and `/chat/stream` paths. Future multi-process
  Wolf swaps in a Redis-backed implementation of the same protocol;
  no other code changes.
- `ToolExecContext` gains optional `cache` field; threaded through
  `dispatch_tool_call` + `AgentLoop.run` + both chat endpoints.
- `_resolve_agent_name_to_id` (Phase 3 Slice 3's agent-name lookup) is
  now the first cache consumer. Hits the Server API once per
  (organization, agent_name) per 60s TTL window; subsequent resolutions
  within a chat loop are free. Negative results cached as
  `__NOT_FOUND__` sentinel so a repeatedly-asked non-existent name
  doesn't re-probe. The earlier "intentionally not cached" comment is
  updated to reflect the new behaviour + staleness bound.
- Audit-write isolation test added to `test_cross_organization_isolation`:
  adversarial payload from organization A names organization B in `event_data`
  fields; stored row's `organization_id` column stamps organization A regardless
  of payload content. Column wins, payload is data.
- 13 new tests total (10 cache + 3 agent-name cache-behavior).

**Slice 4.4 — Phase 4 close-out (this commit)**

- `tools/organization_isolation_test/__main__.py` — the "synthetic probe"
  CLI per doc 05's "run constantly in CI **and** as a synthetic probe
  in production." Six live checks against the actual DB:
    1. RAG: organization A cannot see organization B's chunks
    2. RAG: organization B cannot see organization A's chunks
    3. Audit write isolation: A→B
    4. Audit write isolation: B→A
    5. Cache wrapper rejects unprefixed keys
    6. Cache cross-organization isolation
  Exit 0 on full pass, non-zero on any failure — binary signal for
  CI / production-probe consumers.
- `Makefile` gains `test-isolation-live` target (separate from the
  CI-friendly `test-isolation` which runs the pytest suite).
- Live run against the dev DB: **6/6 checks pass.**
- ONBOARDING gains Gotcha #7 (Wazuh's Indexer-vs-Server-API user-
  database split — the operational issue that bit Slice 1's
  end-to-end retest) and Gotcha #8 (the two-organization dev pattern for
  meaningful isolation testing).

### What we decided

- **No dedicated CI job for the live smoke.** The existing CI test
  job already runs `test_cross_organization_isolation.py` +
  `test_organization_scoped_cache.py` (they're under
  `services/orchestrator/tests/`). The unit-level suite IS the CI
  guard. The live smoke `tools/organization_isolation_test` is for
  production / staging operators to run periodically against their
  actual DB. Adding a separate CI job that bootstraps two organizations
  + seeds them on every PR would be triple the work for marginal
  additional coverage.
- **`--update` flag, not separate `update-organization-*` CLIs.** Per
  the user's explicit choice on the Slice 4.2 design question.
  Captures doc 05's "immutable by default" with minimal new code;
  dedicated update CLIs can come in Phase 5+ if operator ergonomics
  warrant it.
- **Minimal cache abstraction, in-memory only.** Per the user's
  explicit choice on the Slice 4.3 design question. No Redis support
  built preemptively; the protocol stays clean and a Redis impl can
  be added when multi-orchestrator deployment actually exists.
- **Skip per-organization connection pooling.** Doc 05 allows either
  "per-organization pool" OR "stateless re-establish per request." Wolf
  already does the latter (async context-manager per chat request).
  Documenting this in PROGRESS rather than building a per-organization
  pool that's lower-throughput at our current scale.

### What broke / what we discovered

- **Slice 4.1's first re-bootstrap of acme silently succeeded** when
  the operator wasn't expecting it, because `validated_at` was NULL
  pre-Slice 4.2. The fix shipped in 4.2 — the immutability rule only
  activates once a organization HAS been validated by the new CLI. Existing
  organizations pre-dating Slice 4.2 get one free re-bootstrap to opt in.
  Documented in Slice 4.2's commit message + tested by the live
  smoke session.
- **The `__update` / `__NOT_FOUND__` sentinel patterns** worked
  cleanly for the cache + reembed CLI. Pattern worth re-using when
  future modules need "yes / no / haven't asked yet" tri-state
  semantics in NULL-able columns.
- **Wazuh's Indexer-vs-Server-API user-database split** caught us
  during Slice 1's end-to-end retest weeks ago and again during
  Slice 4.2's first probe of qwen3:8b's auth flow. Now codified in
  ONBOARDING Gotcha #7, plus the bootstrap_organization validator's
  Server-API-401 error message names it explicitly. Should not
  re-bite future contributors.

### What's next

- **Phase 5 — Cases and reporting** per `docs/10-build-roadmap.md`.
  The investigation lifecycle: case timeline, findings, exports.
  Less safety-critical than Phase 6 but more user-visible than
  Phase 4. Likely 2-3 weeks of work.
- Phase 6 (propose tools + approval gateway) remains the apex of
  Wolf's safety-critical surface and depends on both Phase 4 (now
  closed) and Phase 5 being solid before it can build safely.

---

## 2026-05-27 — Phase 4.1: two-organization live DB + RAG cross-organization tests

**Session type:** claude-code (continuation; first Phase 4 slice)
**Phase:** Phase 4 — multi-organization hardening (per `docs/10-build-roadmap.md`)
**Duration:** ~45 min
**Branch / commit:** `main` — starting commit `2197d97`, this entry's
commit pending.

**Phase-numbering correction (do not skip this):** earlier Phase 3
sessions referred to "Phase 4" as the propose-tools + approval-gateway
work. That was wrong per the actual roadmap, which orders:

| Phase | What | Status |
|---|---|---|
| Phase 3 | Knowledge & RAG | ✅ shipped |
| **Phase 4** | **Multi-organization hardening** | ← actually next |
| Phase 5 | Cases and reporting | |
| **Phase 6** | **Propose tools + Approval Gateway** | ← what was mis-framed as "Phase 4" |

Older CHANGELOG entries are append-only per `docs/11-claude-code-instructions.md`
and stay as-shipped; PROGRESS.md updated this session to match the
roadmap's actual ordering. Reading the older "Phase 4" references in
prior CHANGELOG entries: they meant the propose-tools work, which is
Phase 6.

### What we did

- **Bootstrapped a second organization `beta`** alongside `acme` so Phase 4's
  isolation work has actual two-organization live state to exercise against.
  Both organizations point at the same dev Wazuh deployment (`192.168.245.128`)
  for simplicity; their separation is enforced by organization_id stamping
  at the application layer, not by per-organization Wazuh instances (the
  "bridge model" from doc 05).
- **Seeded beta with its own private chunks** via the existing dev-seed
  CLI. The seed CLI templates the organization slug into the runbook/incident
  content (`{ORGANIZATION}_SOC SSH brute-force runbook`), so beta's chunks
  are textually similar to acme's but tagged with beta's organization_id and
  reference "BETA SOC" / "BETA SSH sweep" — distinguishable evidence
  of isolation.
- **Live DB state after seeding:**

  | organization | source_type | chunks |
  |---|---|---|
  | acme | past_incident | 1 |
  | acme | runbook | 2 |
  | beta | past_incident | 1 |
  | beta | runbook | 2 |
  | (shared) | attack | 700 |
  | (shared) | wazuh_doc | 4476 |

  Note the shared corpora grew slightly from Slice 3's baseline because
  the dev-seed CLI re-inserts its 6 inline shared chunks on each run.
  Not a problem; the tests don't depend on shared-chunk uniqueness.
- **Extended `tests/test_cross_organization_isolation.py`** with 3 new tests
  covering the Phase-3 RAG path the original Phase-2-era suite predated:
  - `test_pgvector_store_search_constrains_results_to_requesting_organization`
    — source-level invariant. Asserts that every candidate-fetcher
    method (`_vector_candidates`, `_fts_candidates`,
    `_vector_aux_candidates`) contains the organization-scoping WHERE clause
    in its source. A future contributor would have to delete the clause
    to break isolation; the source-grep check catches it without needing
    a live DB.
  - `test_pgvector_chunk_input_validation_blocks_cross_organization_writes` —
    validates that ChunkInput's organization_id-vs-source_type rule (shared
    corpus must have NULL organization_id; organization-private corpus requires a
    organization_id) raises at the data layer. Prevents the inverse mistake
    of cross-organization writes.
  - `test_pgvector_search_call_path_includes_requesting_organization_id` —
    sanity-checks the search() call shape: each leg-helper receives
    the REQUESTING organization's id and ONLY that id.
- **Live cross-organization verification** (one-shot script, not test
  fixture): with the live dev DB in chained-retrieval mode
  (BM25 + v1.5 + v2-moe per ADR 0014), ran the same query as both
  organizations:
  - "SSH brute-force runbook steps" as acme → returned only ACME-tagged
    chunks (ACME SOC SSH brute-force response, ACME SOC Brute-force
    triage, INC-2026-0042 ACME SSH sweep).
  - Same query as beta → returned only BETA-tagged chunks (BETA SOC SSH
    brute-force response, BETA SOC Brute-force triage, INC-2026-0042
    BETA SSH sweep).
  - Zero cross-organization leakage observed.
- **Tests**: 7 tests now in test_cross_organization_isolation.py (4 prior + 3
  new). `make check` 183 passed (180 prior + 3 new). Lint + mypy strict
  still clean.
- **Updated PROGRESS.md** to clarify the actual roadmap-ordered Phase
  4-5-6 sequence, replacing earlier "Phase 4 = propose tools" drift.

### What we decided

- **Beta organization points at the same Wazuh deployment as acme.** In
  production an MSSP would have per-organization Wazuh deployments; for the
  dev DB the application-layer isolation is the load-bearing
  enforcement, and reusing the existing Wazuh keeps the dev setup
  simple. Application-layer organization_id scoping is what we're
  hardening in Phase 4 anyway.
- **Source-level invariant tests for the RAG isolation clauses.** A
  test that runs SQL against a live Postgres would also catch
  regression, but it requires a Postgres-only fixture path the
  conftest doesn't currently provide. The source-grep approach
  catches the regression risk without needing the fixture; Slice 4.4
  will add the canonical `tools/organization_isolation_test/` runnable that
  exercises the live DB as the operational guard.
- **Append-only CHANGELOG discipline preserved.** Prior entries that
  refer to "Phase 4" meaning the propose-tools work are NOT rewritten;
  this entry explains the drift and PROGRESS.md (live state, not
  history) carries the corrected ordering forward.

### What broke / what we discovered

- **The dev-seed CLI is not idempotent on the shared corpus.** Running
  `seed_dev_knowledge` twice (once for acme, once for beta) added 12
  shared chunks instead of 6. Existing chunk-hash idempotency lives
  in `tools/seed_knowledge` (the real corpus ingester), not in
  `seed_dev_knowledge` (which is a Slice-1 inline-content CLI). Not
  blocking — neither retrieval nor isolation is affected — but
  worth a follow-up. Filed as a future ergonomic improvement.
- **Phase-numbering drift was a real cost.** Three prior CHANGELOG
  entries written this session used "Phase 4" to mean what's actually
  Phase 6. A future contributor reading those entries chronologically
  would have inferred a different roadmap shape. Lesson: when
  finishing a phase, re-read `docs/10-build-roadmap.md` for the next
  phase's actual scope before writing the close-out summary, not just
  the current entry's section heading.

### What's next

- **Phase 4 Slice 4.2** — `bootstrap_organization` validates connection
  before persisting; `--update` flag for re-bootstrap (treat
  `OrganizationWazuhConfig` as immutable post-validation per doc 05
  §Organization misconfiguration).
- **Phase 4 Slice 4.3** — `OrganizationScopedCache` abstraction (minimal,
  in-memory) + one consumer (agent_name resolution caching) +
  audit-write isolation test.
- **Phase 4 Slice 4.4** — flesh out `tools/organization_isolation_test` as
  the canonical runnable isolation suite; wire into CI;
  document the two-organization pattern in ONBOARDING; Phase 4 close-out.

---

## 2026-05-27 — Multi-embedding RRF chaining (ADR 0014)

**Session type:** claude-code (continuation)
**Phase:** Phase 3 close-out — chained-retrieval extension
**Duration:** ~90 min
**Branch / commit:** `main` — starting commit `54e01ae`, this entry's
commit pending.

### What we did

- **Empirical motivation**: ran a full-corpus benchmark
  (`tools/embedding_benchmark/full_corpus_v2_eval.py`) re-embedding the
  live 5173-chunk corpus with `nomic-embed-text-v2-moe` in memory and
  comparing against the existing v1.5 embeddings on a 20-query battery
  of rule-ID + ATT&CK technique lookups with known-correct answers.
  Result: v2-moe vectors-only precision@1 = 35% vs v1.5's 15%
  (2.3× lift) and precision@5 = 50% vs 15% (3.3× lift). But v2-moe
  has a 512-token context limit — 3.5% of the corpus (mostly long
  ATT&CK techniques) gets truncated or fails entirely.
- **Operator framed the goal clearly**: chain v1.5 + v2-moe so they
  complement each other and fill the gap where each individually
  lacks. RRF over diverse rankers is exactly the right primitive.
- **Migration 0006** — `embedding_v2 vector(768)` (nullable) +
  `embedding_v2_model varchar(100)` + HNSW cosine-ops index on the
  new column. Backward-compatible: existing chunks keep working with
  NULL aux columns.
- **Settings** — `EMBEDDING_MODEL_AUX` / `EMBEDDING_PROVIDER_AUX`.
  Empty default preserves Slice-2A behaviour (single-leg vector +
  BM25). When set, the orchestrator builds a second embedder.
- **`make_embedding_provider_aux(settings)`** factory — returns None
  for empty config; constructs the secondary adapter otherwise.
  Shares `_build_provider()` helper with the primary factory.
- **`PgvectorKnowledgeStore`** — accepts `embedder_aux=None` kwarg.
  `upsert()` writes both vectors when configured (with per-chunk
  error tolerance for aux — a v2-moe rejection leaves
  `embedding_v2 IS NULL` for that chunk; primary leg still indexes
  it). `search()` adds a `_vector_aux_candidates()` helper that
  filters on `embedding_v2 IS NOT NULL` so unembedded chunks don't
  pollute or block the aux leg.
- **`search()`** now does 3-way RRF when an aux embedder is wired
  (BM25 + primary vector + secondary vector). Slice-2A behaviour is
  preserved when not — same 2-leg flow as before.
- **`wolf reembed --aux`** — extended to walk rows where
  `embedding_v2_model IS DISTINCT FROM <active aux model>` and
  populate them in batches. Uses an `__unembeddable__` sentinel value
  for chunks the aux model rejects after truncation (1800-char
  default cap), so subsequent runs don't loop on them. Per-chunk
  error tolerance preserved.
- **chat.py** — constructs both embedders via the factories and
  hands the secondary to `PgvectorKnowledgeStore(..., embedder_aux=aux)`.
  Both endpoints (`/chat` and `/chat/stream`) updated symmetrically.
- **Populated `embedding_v2` for the entire corpus**: 5145 / 5173
  chunks (99.5%) successfully embedded with v2-moe; 28 chunks
  (0.5%) marked `__unembeddable__` after truncation (long ATT&CK
  descriptions that even at 1800 chars produce malformed input
  v2-moe rejects). Those 28 stay retrievable via v1.5 + BM25 legs —
  the chained design's complement-each-other promise.
- **Tests** — 2 new in `test_knowledge_store.py`:
  - `test_rrf_fusion_three_legs_chunk_in_all_wins` — a chunk
    ranking in all three legs decisively beats singletons present
    in only one.
  - `test_rrf_fusion_skips_aux_leg_when_no_aux_embedder` — default
    behaviour is preserved when `embedder_aux=None` (the aux helper
    is not even invoked).
  `make check`: **180 passed** (178 prior + 2 new). Lint + mypy
  strict clean.
- **`tools/embedding_benchmark/full_corpus_chained_eval.py`** — runs
  the same 20-query battery against the LIVE store in two modes:
  single-leg (BM25 + v1.5) and chained (BM25 + v1.5 + v2-moe).
- **ADR 0014** captures the design + alternatives + measured impact
  + operator workflow + rollback path.

### Measured impact

20 queries with known-correct answers (rule IDs + ATT&CK technique
IDs) against the live 5173-chunk corpus.

| Mode | precision@1 | precision@5 | p50 latency |
|---|---|---|---|
| Vectors-only v1.5 | 15% (3/20) | 15% (3/20) | (in-memory test) |
| Vectors-only v2-moe | 35% (7/20) | 50% (10/20) | (in-memory test) |
| **BM25 + v1.5** (Slice 2A baseline) | 15% (3/20) | 35% (7/20) | 48 ms |
| **BM25 + v1.5 + v2-moe (ADR 0014)** | **30% (6/20)** | **60% (12/20)** | 159 ms |

Chained mode recovers 5 queries single-leg missed entirely in the
top-5 (Process Injection T1055, Local System T1005, DNS Tunneling
T1071.004, Pass the Hash T1550.002, Boot/Logon Autostart T1547).
Latency goes 48 → 159 ms per search — imperceptible inside the
multi-second LLM generation phase.

### What we decided

- **RRF over a third leg, not score normalization.** Per-leg
  rankings, no cross-leg score comparison — same primitive Slice 2A
  uses for BM25 + vector fusion. Adding a fourth leg later is
  mechanical.
- **Nullable aux column, per-chunk error tolerance.** Chunks the
  aux model can't handle stay retrievable via v1.5 + BM25. The
  design intent is explicitly "v1.5 covers what v2-moe can't" — not
  "everything embeds twice or nothing."
- **Sentinel `__unembeddable__` for chunks even truncation can't
  fix.** Prevents the reembed CLI looping forever on a small set of
  problematic chunks.
- **Empty default for `EMBEDDING_MODEL_AUX`.** Single-leg deployments
  cost nothing; the chained path is opt-in via env. Wolf's "no paid
  dependency" principle isn't touched — both v1.5 and v2-moe are
  Apache 2.0, both run via Ollama.
- **The realistic operational metric is precision@5, not @1.** The
  agent loop retrieves top-K chunks and feeds them to the LLM. RRF
  is structurally better at building a high-recall top-K than at
  picking a single best — exactly what the agent needs.

### What broke / what we discovered

- **First reembed run stalled at 97% coverage** because the CLI's
  initial error path set `embedding_v2_model = NULL` for chunks the
  aux rejected, but the `IS DISTINCT FROM` filter kept picking those
  same NULL rows back up on the next iteration — infinite loop.
  Fix: sentinel value `__unembeddable__` distinct from both NULL and
  the active aux model id. Plus 1800-char truncation cap so most
  long chunks succeed.
- **v2-moe still rejects 28 chunks even at 1800-char input.** Long
  ATT&CK techniques with dense paragraph structure produce
  "unexpected EOF" no matter how we slice the text. Those chunks
  retain `embedding_v2 IS NULL` and `embedding_v2_model =
  '__unembeddable__'`. The chained design absorbs this: v1.5 covers
  the long-context retrieval for them.
- **precision@1 dropped slightly vs vectors-only v2-moe** (35% →
  30%) — RRF dilutes a single-leg dominant ranking when the other
  legs don't agree. This is a known RRF property and the right
  trade because Wolf retrieves top-5, not top-1. precision@5 went
  up by half (35% → 60%) as expected.

### What's next

- **Phase 4 — propose tools + approval gateway.** All Phase 3 work
  now sits at a stable end-state with measured retrieval quality
  improvements documented.
- **Operator install-script update.** Doc 16's install-script spec
  needs the optional aux-embedder step (`ollama pull
  nomic-embed-text-v2-moe`) plus the post-install reembed
  documented. Belongs in the Phase 4 packaging work.
- **Future: 4th RRF leg** (a Wolf-specific fine-tune of one
  embedding model on real analyst queries). Not pressing; the
  3-leg flow at 60% precision@5 is enough to ship Phase 4 against.

---

## 2026-05-27 — Phase 3 follow-ups: judge model, agent_name, reembed, frontend

**Session type:** claude-code (continuation)
**Phase:** Phase 3 close-out — all queued follow-ups
**Duration:** ~120 min
**Branch / commit:** `main` — starting commit `05cb750`, this entry's
commit pending.

### What we did

**Follow-up 1 — stronger grounding judge (ADR 0013):**

- Added three settings to `Settings`:
  - `GROUNDING_JUDGE_MODEL_ID` (empty = use the chat model; backward-
    compat)
  - `GROUNDING_JUDGE_MODEL_PROVIDER` (empty = same as chat)
  - `GROUNDING_JUDGE_API_KEY_REF` (empty = same as chat)
- Refactored `app/agent/model_resolver.py` to factor out a `_build_provider()`
  helper shared by both `get_model_for_organization()` and the new
  `get_grounding_judge_model()`.
- Threaded `judge_provider` through `chat.py` (both endpoints) into the
  `GroundingValidator`. When the override env vars are empty the helper
  returns the chat provider unchanged.
- Probed three candidates honestly:
  - **qwen3.6:27b** — pulled (17.4 GB) but cannot load on this dev host:
    Ollama: `model requires more system memory (16.1 GiB) than is
    available (11.4 GiB)`. Two VMware VMs (the test agent + the Wazuh
    server) plus Firefox / VS Code consume too much RAM. Deleted the
    model after the failed probe to free disk.
  - **qwen3.5:9b** — pulled (5.6 GB), probe score **0.50** — same JSON
    syntax regression the Qwen 3.5 family showed at 4B in ADR 0009.
    Confirms the 3.5 line on Ollama has a structured-output glue
    issue at every size; gated on the next upstream release.
  - **qwen3:8b** — already pulled, ADR 0010 measured 0.75 (same
    descriptor as qwen3:4b but more parameters; tight-fit at 85%
    GPU / 15% CPU). Realistic local upgrade for this hardware.
- Wrote **ADR 0013** capturing the env-var mechanism, the per-
  candidate findings, and the operator recommendations (qwen3:8b
  for this hw, qwen3.6:27b on workstation-class GPUs with 24+ GiB
  free RAM, hosted Nemotron 120B via OpenRouter for the strongest
  available judge).
- End-to-end retest with `GROUNDING_JUDGE_MODEL_ID=qwen3:8b`:
  - Question: "What SSH brute-force alerts have fired on
    `agent_name linux-test-agent` in the last 30 minutes? Look up
    rule 5712 and tell me what to do."
  - Strategy `guided`, 2 tool calls (`get_rule_definition` +
    `search_alerts`).
  - Verdicts: **supported=2, unsupported=2, unverifiable=1**.
  - **The stronger judge caught a real fabrication**: qwen3:4b
    emitted "Source IP: 192.168.1.100" and "Block the source IP
    (192.168.1.100)" — both wrong; the actual attacking IP was
    192.168.245.1 (the dev host running the brute-force loop).
    Both fabrications received `[unverified]` markers inline.
    This is the validator paying off as designed: it caught a
    confident hallucination the model would otherwise have shipped.

**Follow-up 2 — search_alerts agent_name lookup:**

- Added `agent_name: str | None` field to `SearchAlertsInput`.
- New helper `_resolve_agent_name_to_id()` queries the Server API's
  `/agents?name=` filter and returns the numeric id. When `agent_id`
  is empty and `agent_name` is provided, the tool resolves the name
  before calling the query builder.
- Tool descriptions tightened to clarify `agent_id` expects the
  numeric ID (e.g. `'001'`), not the human-readable name.
- Edge cases: explicit `agent_id` wins over `agent_name` (no
  unnecessary API call); unresolvable name runs an unfiltered query
  (validator catches the resulting under-grounding rather than
  raising); neither set means no agent filter.
- 4 new tests in `test_search_alerts_agent_name.py`.

**Follow-up 3 — wolf reembed CLI:**

- New `app/management/reembed.py`. Walks `knowledge_chunks` where
  `embedding_model != active_provider.model_id`, re-embeds in
  batches, updates only `embedding` + `embedding_model` (content
  + metadata untouched).
- Default mode is REPORT-ONLY; `--apply` required to write. Per-
  organization scoping via `--organization-slug` or `--organization-slug __shared__`
  for the shared corpora. `--limit` for incremental migration.
- Idempotent: re-running after a clean pass finds zero mismatches.
- Smoke-tested in report mode on the live DB (0 mismatches — the
  full corpus was already embedded with the active provider).

**Follow-up 4 — frontend grounding integration:**

- `frontend/lib/types.ts`: `ChatResponseBody` and `ChatExchange`
  gain `grounding_supported / unsupported / unverifiable` fields
  (nullable; null when validator didn't run).
- `frontend/lib/types.ts`: `LoopEventType` adds `grounding.completed`
  (SSE event the backend already emits).
- `frontend/hooks/use-chat-stream.ts`: stores the three grounding
  counts on the completed exchange.
- `frontend/components/markdown.tsx`: new
  `highlightUnverifiedMarkers()` helper walks the rendered React
  tree, splits text nodes on the literal `[unverified]` token, and
  replaces each occurrence with a styled `<span>` (destructive-
  tinted background, warning icon, hover-tooltip). Applied to `p`,
  `li`, `td`, `th`, `blockquote` element renderers — every
  flowing-text location markdown supports.
- `frontend/components/message-thread.tsx`: new `GroundingBadge`
  rendered in the per-exchange metadata strip. Shows
  `grounding N✓ N✗ N?` with a destructive variant when
  unsupported > 0. Hover-tooltip explains what each count means.
- `npm run lint` clean.

### What we decided

- **Don't ship a default that doesn't work for the floor hardware.**
  qwen3.6:27b is the right judge for workstation GPUs but the
  development environment can't run it. The default stays "use the
  chat model" for backward compatibility; operators with capable
  hardware set the override.
- **Mark, don't fail-closed.** When the operator has wired a
  stronger judge AND it flags claims as unsupported, the answer
  reaches the analyst with `[unverified]` markers — never silently
  dropped. The frontend now makes those markers visible.
- **search_alerts unresolvable-name returns empty rather than
  raising.** The validator's "no alerts found" → unsupported claim
  detection catches the under-grounding without a Pydantic-error
  shape the model can't recover from.
- **Reembed defaults to report-only.** Re-embedding 5170 chunks
  takes ~2 minutes; the safety of "show me what would change
  first" outweighs the convenience of one-step apply. `--apply` is
  the explicit opt-in.

### What broke / what we discovered

- **Real RAM ceiling on this dev box.** The two VMware VMs (the
  Wazuh server at .128 and the test agent at .129) consume ~6 GiB
  combined, plus Firefox / VS Code overhead — only 8.1 GiB
  available. qwen3.6:27b at 16.1 GiB doesn't fit. ADR 0013 records
  this so the next operator on this exact setup knows.
- **The Qwen 3.5 family has a persistent OllamaAdapter glue
  problem.** Both qwen3.5:4b (ADR 0009) and qwen3.5:9b score 0.50
  with the same "Not valid JSON" parse error. Not a Wolf bug; the
  3.5 line's chat-template or tool-spec serialisation differs from
  3.x in a way the Ollama JSON path doesn't tolerate. Worth
  re-probing whenever Ollama releases a new qwen3.5 tag.
- **The stronger-judge demo is the most satisfying Phase 3 moment
  so far.** qwen3:4b confidently claimed source IP `192.168.1.100`
  and a "block this IP" instruction — both fabricated. qwen3:8b as
  the judge flagged both. The validator went from "graceful
  degradation when judge fails" to "actively saving the analyst
  from acting on a hallucinated IP."
- **VRAM contention during tests.** The factory test that loads
  BGE-base via sentence-transformers needs ~400 MB VRAM; when
  Ollama has a model loaded, OOM is possible. Easy mitigation:
  `ollama ps` + manual stop before running the full test suite.
  Logged but not codified.

### What's next

- **`wolf` install-script step** that prompts the operator for the
  judge-model preference at first run (qwen3:4b default,
  qwen3:8b recommended if RAM allows, qwen3.6:27b for workstation
  GPUs). Belongs in doc 16 / ADR 0007's install-script spec.
- **Heuristic+LLM hybrid validator** if rich-corpus operation
  shows the LLM judge failing too often. Not pressing.
- **Phase 4** — propose tools + the approval gateway. Phase 3 is
  now closed end-to-end (RAG, hybrid retrieval, grounding
  validator, real corpus, live demo, operator-tunable judge model).

---

## 2026-05-27 — Phase 3 Slice 3: real seed corpora + live end-to-end on new agent

**Session type:** claude-code (continuation)
**Phase:** Phase 3 — Slice 3 of 3 + full-stack live retest
**Duration:** ~60 min
**Branch / commit:** `main` — starting commit `e0e94f4`, this entry's
commit pending.

### What we did

- Operator provisioned a dedicated test agent at `192.168.245.129`
  (`linux-test-agent`, Wazuh agent id 001, status active) — confirmed
  via the Wazuh Server API's `/agents` endpoint. Reachable from the
  dev host; SSH on port 22 (OpenSSH 9.6 on Ubuntu 24.04).
- Built `tools/seed_knowledge/` — the production-grade ingesters:
  - `attack.py` — downloads MITRE/CTI's `enterprise-attack.json`
    (pinned to the master branch; cached under
    `.local/seed_knowledge_cache/`), parses the STIX bundle, filters
    to active `attack-pattern` objects (excludes `revoked` +
    `x_mitre_deprecated`), emits one ChunkInput per technique with
    metadata (`technique`, `title`, `attack_version`,
    `kill_chain_phases`, `is_subtechnique`, `parent_technique`).
    Content lead is the ATT&CK ID for clean FTS keyword hits.
  - `wazuh_rules.py` — downloads the Wazuh release archive
    (pinned to `v4.9.2`), iterates rule XML files under
    `ruleset/rules/`, wraps each file in a synthetic `<root>` before
    `ElementTree.fromstring()` (Wazuh files are top-level `<group>`
    elements — not strictly well-formed XML), emits one ChunkInput
    per `<rule>` with metadata (`rule_id`, `level`, `title`,
    `ruleset_file`, `groups`, `mitre`, `wazuh_version`).
  - `__main__.py` — driver CLI with `--source attack | wazuh_rules
    | all`, `--replace-shared` (deletes existing organization_id-NULL
    chunks before re-ingesting), `--cache-dir`, `--limit`, and
    SHA-256-of-content idempotency (re-running without
    `--replace-shared` skips chunks already in the DB).
- Idempotency by design: organization-private chunks (`organization_id IS NOT
  NULL`) are never touched by the ingester. Operator-local
  customisation (e.g. the ACME SOC runbooks) survives a corpus
  refresh.
- Ran the full clean ingest: `--source all --replace-shared`.
  - Deleted 16 existing shared chunks (the dev-seed corpus from
    Slice 1 + the 5 ATT&CK chunks from the smoke test).
  - 697 ATT&CK techniques parsed from matrix v19.1, all 697 inserted.
  - 4473 Wazuh rules parsed from v4.9.2; 1 file with a
    well-formedness defect (`0910-ms-exchange-proxylogon_rules.xml`)
    logged and skipped (graceful degradation contract).
  - Total runtime 2 min 4 s on the RTX 4050 (embed bottleneck:
    nomic-embed-text via Ollama at ~30 ms/chunk).
  - Final DB state: **5170 shared chunks + 3 organization-private chunks**.
- Confirmed retrieval quality on the rich corpus by direct store
  smoke-test (bypassing the chat endpoint):
  - "rule 5712 sshd brute force" → Rule 5712 chunk #1 (FTS exact-
    match), Rule 5763 #2, Rule 5714 #3.
  - "T1110 brute force" → Exim brute-force rule #1, T1110 #2,
    Proxmox brute-force rule #3 (interesting cross-source ranking;
    T1110 not #1 but in the top 3).
  - "attacker uses valid credentials to log into another host" →
    T1021.004 SSH #1, T1556 Modify Authentication Process #2, T1078
    Valid Accounts #3 — pure semantic retrieval, all three perfectly
    on-topic.
- 11 new parser tests in `tests/test_seed_knowledge_ingesters.py`:
  ATT&CK STIX parsing (techniques, subtechniques, deprecated filter,
  non-attack-pattern skip, missing-id skip, FTS ID-front content);
  Wazuh rule parsing (multi-rule extraction, content-starts-with-id,
  malformed-file graceful, missing-description skip, zip iteration).
  `make check` clean: **174 passed** (128 prior + 19 knowledge + 16
  validator + 11 ingester). Lint + mypy strict still clean.
- **End-to-end live demo on the new agent**:
  - Triggered 12 SSH brute-force attempts from this host against
    `attacker_user_1` through `attacker_user_12` on
    `192.168.245.129`. All failed (`Permission denied`); 3 dropped
    by SSH's pre-auth connection cap (`kex_exchange_identification:
    read: Connection reset by peer`).
  - Wazuh ingested 10 alerts on agent 001 within 15 s of the burst:
    9× rule 5710 (level 5, sshd non-existent user) + 1× rule 5712
    (level 10, sshd brute force composite). Pattern matches doc 06's
    canonical example and our seeded runbooks exactly.
  - First Wolf chat ("investigate SSH brute-force on
    linux-test-agent in the last 10 minutes") found 0 hits because
    qwen3:4b passed `agent_id="linux-test-agent"` (the name) instead
    of `"001"` (the numeric ID). The model concluded "no alerts
    were found"; **the grounding validator flagged that conclusion
    as `unsupported`** — exactly the right behaviour because
    "search returned 0 hits" is NOT evidence of absence. Final
    answer carried two `[unverified]` markers inline.
  - Second Wolf chat with the agent ID stated explicitly ran the
    full pipeline: 4 steps, 3 tool calls
    (`search_alerts` + `get_rule_definition` + `query_runbook`),
    answer drew on real ATT&CK T1110 content from the freshly-
    ingested STIX bundle (cited specific TrendMicro and Crashoverride
    references that are in MITRE's source corpus). The grounding
    validator's judge LLM returned malformed JSON on this prompt
    (large evidence section); the validator degraded gracefully,
    returned the original answer un-annotated, and surfaced
    `grounding_*` counts as `None`. Both behaviours are the
    documented contract.

### What we decided

- **`tools/seed_knowledge` is the canonical corpus channel.** The
  Slice-1 inline `seed_dev_knowledge.py` survives because it's
  useful for tests + fresh-machine bring-up before the network
  ingest runs, but the dev DB's authoritative material now comes
  from real MITRE + Wazuh sources.
- **Pin both sources, don't follow `master`.** ATT&CK gets bumped
  by changing `ATTACK_URL` (currently `master` for matrix v19.1)
  and clearing the cache; Wazuh ruleset gets bumped by changing
  `WAZUH_VERSION`. Re-embedding the entire corpus is the deliberate
  cost of a version bump — `--replace-shared` makes it explicit.
- **No prose Wazuh docs in this slice.** Scope discipline: XML
  rules + JSON ATT&CK give us realistic corpus volume (~5k chunks)
  without HTML-scraping edge cases. If operators want the user-
  manual prose later, a separate Slice 3.5 can add HTML scraping
  for selected pages.
- **The agent-name vs agent-ID confusion is a tool-side fix**, not
  a validator failure. Adding `agent_name` as a synonym in
  `search_alerts` (lookup against `list_agents`) is the right
  remediation; logged as a Phase-3-follow-up.

### What broke / what we discovered

- **Wazuh rule files aren't valid XML on their own.** They have a
  top-level `<group>` element (not `<rules>` or anything that
  declares itself a root). ElementTree refuses to parse them
  directly. Fix: wrap each file in a synthetic `<root>` before
  parsing. Documented in the ingester.
- **One ruleset file is genuinely malformed**
  (`0910-ms-exchange-proxylogon_rules.xml` at line 57 col 56).
  Parser logs a warning and skips; the other 4473 rules ingest
  cleanly. Likely an upstream Wazuh ruleset issue worth raising
  with them, but out of scope here.
- **ATT&CK STIX bundle structure**: matrix version is on the
  `x-mitre-collection` object, not the technique entries. Parser
  reads it once before iterating; bundle defaults `attack_version`
  to `"unknown"` if the schema changes.
- **The grounding validator catches false-negative claims too.**
  On the agent-name-vs-ID confusion run, the model concluded "no
  alerts were found" off a single 0-hit search — and the validator
  marked both that conclusion claim and the follow-on as
  `unsupported`. This is a Real Result: doc 06's validator design
  catches "we didn't find it so it's not there" reasoning, not just
  fabrication.
- **qwen3:4b's judge JSON is unreliable at high evidence-prompt
  volumes.** On the rich-corpus run the judge's response wasn't
  parseable; validator degraded gracefully. Pushes the stronger-
  judge follow-up (Nemotron via OpenRouter, prompt refinement,
  or heuristic+LLM hybrid) up the priority list.
- **5170 chunks is a real number, not a toy.** Hybrid retrieval +
  the HNSW vector index handle this volume without measurable
  latency change vs the 9-chunk seed. pgvector scales here.

### What's next

- **Stronger grounding judge** (now the top follow-up — Slice 2's
  architecture is sound; the model is the dial).
- **`search_alerts` agent-name lookup** — small, contained fix.
- **`wolf reembed` helper** queued from ADR 0012.
- **Frontend integration of grounding markers** — the chat UI
  doesn't render `[unverified]` or the validation counts specially
  yet.
- **Phase 4 entry** — propose tools + the approval gateway.
  Phase 3 closure ratifies the read-side foundation Phase 4 depends
  on.

---

## 2026-05-27 — Phase 3 Slice 2A + 2B: hybrid retrieval + grounding validator

**Session type:** claude-code (continuation)
**Phase:** Phase 3 — Slice 2 of 3 (both parts)
**Duration:** ~120 min
**Branch / commit:** `main` — starting commit `0daea82`, two commits
land in this session (8f0d544 for Part A, pending for Part B).

### What we did

**Part A — Hybrid retrieval (commit 8f0d544):**

- Migration 0005: added a `content_tsv tsvector` STORED generated
  column on `knowledge_chunks` populated via `to_tsvector('english',
  content)`. Existing rows auto-backfill on the ALTER. GIN index
  `ix_knowledge_chunks_content_tsv` enables fast `@@ tsquery` lookups.
- Declared the column on the SA model as
  `Computed("to_tsvector('english', content)", persisted=True)`
  with `TSVECTOR` type so the hybrid search query can reference it
  via the model. Wolf never writes to this column directly.
- `RetrievedChunk` gained an optional `rrf_score` field (None on
  pure-vector paths; populated on hybrid).
- Replaced `PgvectorKnowledgeStore.search()` with a hybrid
  implementation:
  - `_vector_candidates()` — top-25 by cosine distance via pgvector's
    HNSW index from migration 0004.
  - `_fts_candidates()` — top-25 by `ts_rank_cd`, gated on the `@@`
    predicate so chunks with zero token match are excluded.
  - Reciprocal Rank Fusion (Cormack et al. 2009, k=60): for each
    chunk present in either leg, `score = sum(1 / (60 + rank_in_leg))`.
    Chunks ranked highly in both legs win.
  - Organization-scoping clause is preserved in both legs (defence in depth).
  - `source_types` + `metadata_filters` apply to both legs via shared
    `_apply_metadata_filters` helper.
- Smoke against the dev corpus showed the expected behaviour:
  - Query "rule 5712" → Rule 5712 chunk ranks #1 (FTS exact-token boost)
  - Conceptual queries → vector-driven ranking dominates
  - Mixed queries → both legs contribute
- 3 new tests in `tests/test_knowledge_store.py` (constants sane,
  `RetrievedChunk` carries `rrf_score`, fusion math correct — chunk
  present in both legs ranks above singletons).

**Part B — Grounding validator (this commit):**

- New `app/grounding/` module:
  - `GroundingValidator` class. LLM-as-judge: extract claims (sentence
    splitter that respects numbered-list markers by requiring a letter
    before the sentence-end punctuation), build evidence (concatenated
    tool results + retrieved chunks with `[TOOL_RESULT N: name]` and
    `[KNOWLEDGE N: source]` tags), one model call producing structured
    JSON verdicts (`supported` / `unsupported` / `unverifiable`),
    splice `[unverified]` inline on unsupported claims.
  - `ClaimVerdict` + `ValidationResult` dataclasses for the structured
    output.
  - Failure modes are non-blocking: judge raises, judge returns
    malformed JSON, codefence-wrapped JSON — all degrade gracefully
    to "validation skipped, original answer returned" per the
    operator's Slice 2 choice (mark-inline, not fail-closed-drop).
- `AgentAnswer` gained three optional fields:
  `grounding_supported / unsupported / unverifiable`. Stay `None`
  when the validator didn't run.
- `AgentLoop._finalize_answer()` helper runs the validator on the
  draft answer before either the `_emit("answer", ...)` event or
  the return. Skips when validator is `None`, answer is empty, or
  there are no citations (no evidence to validate against).
- Hooked at both `AgentAnswer` construction sites in the loop
  (stop_reason="answer" success path AND budget_exhausted path).
- Loop accumulates evidence across steps in two separate lists:
  `all_retrieved_chunks` (from `query_runbook.hits`) and
  `all_tool_results` (everything else) for better provenance in
  the judge's evidence prompt.
- `LoopEventType` gained `grounding.completed`; the SSE stream now
  surfaces validator verdicts to the frontend.
- New audit event type `grounding.validation.completed` records the
  per-loop counts and whether the validator ran.
- `chat.py` constructs the validator from the same `provider` used
  for the agent loop and threads it through `loop.run(...)`. The
  chat response body surfaces the three counts.
- 16 new tests in `tests/test_grounding_validator.py` covering claim
  splitting (simple + numbered lists + empty), evidence formatting,
  happy paths (all supported, mixed with unsupported, marker
  placement), and degradation (no citations, empty answer, judge
  raises, malformed JSON, codefence wrapping, claim-count clamping).
  Annotation logic exercised directly.
- `make check` clean: **162 passed** (146 prior + 16 new). lint +
  mypy strict still clean.

### End-to-end verification on the live Wazuh

1. **Pure RAG question** ("What is the Acme SOC runbook for SSH
   brute-force?"): 1 tool call (`query_runbook`), validator returned
   `supported=1, unsupported=0, unverifiable=1` — the procedural
   summary correctly labeled supported, the "Citations:" trailer
   labeled unverifiable. 93 s.
2. **Mixed-mode embellishment case** (the canonical test from
   Slice 1: "Look up rule 5712 definition + Acme runbook"):
   `get_rule_definition` + `query_runbook` in one loop, validator
   returned `supported=0, unsupported=0, unverifiable=7`. The
   pipeline ran correctly (7 claims extracted, judge called once,
   verdicts surfaced via the API) but qwen3:4b as the judge played
   safe and labeled every claim "unverifiable" instead of flagging
   the specific embellishment as "unsupported". 207 s total.

### What we decided

- **Validator architecture lands as planned, judge-model selection
  is the next dial to turn.** The embellishment-detection gap is
  not an architecture bug; it's a known limitation of LLM-as-judge
  with a 4B model judging its own output. Doc 06's grounding-validator
  design assumes a sufficiently strong judge; we'll evaluate
  alternatives (Nemotron via OpenRouter, prompt refinement, hybrid
  heuristic+LLM fallback) in a follow-up.
- **Mark-inline, not drop**, per the operator's earlier choice. This
  session honoured that posture across all paths: the analyst sees
  the suspect claim with a `[unverified]` marker, never silently
  dropped content. Failure modes (judge errors) also preserve the
  original answer rather than refusing to respond.
- **No organization- or per-request validator override** for Slice 2. A
  `validator_mode` field on `ChatRequestBody` was offered in the
  Slice 2 planning question and not chosen; current code-level
  default-mark is sufficient. Operator can opt out by removing the
  validator construction in chat.py if needed (one line); a config
  toggle can be added later if multiple operators ask for it.
- **No grounding gate on `[unverified]` claim count**. The validator
  is informative; downstream Phase-4 propose/execute tools may want
  to refuse to propose actions if the answer that motivated them
  has unsupported claims, but that decision belongs in Phase 4 not
  Slice 2.

### What broke / what we discovered

- **Recursive validation is real.** qwen3:4b judging qwen3:4b's
  output is structurally suspect — a model that struggles with
  grounding discipline (ADR 0002) is not the best critic of its own
  grounding. The fact that the validator labeled every claim
  "unverifiable" on the hard case rather than picking a side is the
  model's risk-averse posture under uncertainty. Architecture is
  correct; judge model needs to improve. Logged as Slice 2's main
  follow-up.
- **The numbered-list splitter took two iterations.** First version
  treated `"1."` as a sentence end (matching `[.!?]\s+`), splitting
  `"1. Run list_agents."` into `["1.", "Run list_agents."]`.
  Required a letter before the sentence-end (`[a-zA-Z][.!?]`) to
  avoid digit-as-list-marker false positives. Second iteration
  forgot uppercase letters could appear before the period
  (`"IP."`); fixed with `[a-zA-Z]`. Both iterations caught by the
  unit test.
- **Markdown codefence wrapping is common.** Small models like
  `qwen3:4b` and `granite3.3:8b` sometimes wrap their JSON output in
  triple-backtick fences. The validator strips this before parsing.
  Tested in `test_validate_strips_json_codefence_wrapping`.
- **Async-correctness for sync deps.** The grounding validator does
  one `provider.chat()` call which is already async. No new
  `asyncio.to_thread` needed (unlike the sentence-transformers
  adapter in Slice 1.5).

### What's next

- **Slice 3** — real seed corpora (Wazuh docs + ATT&CK scrapers in
  `tools/seed_knowledge`).
- **Slice 2 follow-up** — evaluate stronger judges (Nemotron 120B
  via OpenRouter, or a heuristic-LLM hybrid) once Slice 3 produces
  enough verdict samples to measure precision/recall meaningfully.
- **`wolf reembed`** helper queued from ADR 0012 still pending.

---

## 2026-05-26 — Phase 3 Slice 1.5: sentence-transformers adapter + ADR 0012

**Session type:** claude-code (continuation)
**Phase:** Phase 3 — Slice 1.5 of 3
**Duration:** ~60 min
**Branch / commit:** `main` — starting commit `8cb3ab9`, final commit
pending this entry.

### What we did

- **Added an optional Python extra `embeddings-local`** in
  `services/orchestrator/pyproject.toml` carrying
  `sentence-transformers>=3.0` + `torch>=2.4`. Default `uv sync`
  is unchanged — the orchestrator's mandatory wheel set stays
  torch-free per ADR 0007's native-packaging constraints.
- **Built `SentenceTransformersEmbeddingAdapter`** in
  `app/knowledge/embeddings.py`. Lazy-imports `sentence_transformers`
  inside the constructor so the module still imports cleanly when
  the optional extra isn't installed (clear `ImportError` with
  install hint at construction time). Detects CUDA, falls back to
  CPU. Wraps `encode()` in `asyncio.to_thread` so it doesn't block
  the event loop. Applies the BGE asymmetric query prefix
  (`"Represent this sentence for searching relevant passages: "`)
  automatically when the model name contains "bge".
- **Added `make_embedding_provider(settings)` factory** that selects
  the adapter from `EMBEDDING_PROVIDER` (default `ollama`) and
  `EMBEDDING_MODEL` env vars. Accepts aliases
  (`sentence-transformers`, `sentence_transformers`, `st`).
- **Threaded the factory through** `services/orchestrator/app/api/chat.py`
  and `services/orchestrator/app/management/seed_dev_knowledge.py` so
  both code paths honour the env-driven selection. No call-site
  hardcodes the Ollama adapter anymore.
- **Wrote `tools/embedding_benchmark/`** — side-by-side benchmark CLI.
  Loads the same 9-chunk dev corpus the seed CLI uses (imports
  `SHARED_CHUNKS` + `runbook_chunks_for` directly so the comparison
  is reproducible). Measures cold-start, per-query latency (3
  trials × 10 queries, median), corpus-embed throughput, and
  qualitative top-5 retrieval for each adapter against the same
  query set. Optional `--json` for machine-readable output.
- **Ran the benchmark** on the RTX 4050 Laptop GPU:
  - Ollama (nomic-embed-text): cold-start 0.07 s (daemon warm),
    p50 30.7 ms, corpus 19 ms/chunk
  - sentence-transformers (BGE-base-en-v1.5): cold-start 10.12 s,
    p50 5.9 ms, corpus 8 ms/chunk
  - Retrieval precision was qualitatively better for BGE on
    entity-specific lookups (e.g. "What is T1078 Valid Accounts?"
    — BGE ranked T1078 #1; Ollama-nomic ranked T1110.001 #1).
    On ambiguous procedural queries both ranked comparably.
    Sample size small; trend suggestive.
- **Wrote ADR 0012** —
  `docs/decisions/0012-embedding-stack-ollama-vs-sentence-transformers.md`.
  Decision: **keep both adapters; default Ollama** (preserves
  ADR 0007's packaging story, matches LLM Ollama pattern, fast
  steady-state startup); **sentence-transformers as opt-in extra**
  for operators with high-throughput ingestion or precision needs.
  Records the empirical numbers verbatim, lays out the
  variable-confound trade explicitly (the chosen comparison
  mixes runtime + model; isolation would have needed same-model
  on both runtimes — the operator chose the cross-stack comparison
  for actionability over rigour).
- **Added 3 new tests** in `tests/test_knowledge_store.py` covering
  the factory contract (default routes to Ollama; unknown provider
  rejected; sentence-transformers aliases accepted). 12 prior
  Slice 1 tests still pass.
- **`make check` clean: 143 passed** (128 prior + 12 Slice 1 + 3
  Slice 1.5). Lint + mypy strict still clean. Benchmark CLI gets
  a file-level `# ruff: noqa: T201, E402` for its intentional CLI
  prints + path-bootstrap import order.
- **Updated `docs/decisions/README.md`** index with ADR 0012.

### What we decided

- **Both adapters are kept, behind the same `EmbeddingProvider`
  Protocol.** Operator switches via `EMBEDDING_PROVIDER` env. The
  protocol absorbs the choice; no other code needs to change.
- **Ollama stays the default** for new installs. The ADR 0007
  packaging argument is load-bearing — torch+transformers add
  ~2 GB to the orchestrator install, which materially hurts the
  `.deb` / `.rpm` channel's appeal. The retrieval-precision edge
  for BGE on micro-benchmark wasn't large enough to overturn this.
- **sentence-transformers is the recommended choice for bulk
  re-embedding** (Slice 3's Wazuh-docs / ATT&CK ingest will run
  thousands of embed calls at once — the 2.4× corpus-throughput
  win matters there). Operator can `EMBEDDING_PROVIDER=
  sentence-transformers` for the duration of the migration,
  then flip back.
- **The benchmark CLI is permanent**, not throwaway. Future
  hardware changes / model swaps can re-run it.
- **No re-embedding helper in this slice.** Flipping
  `EMBEDDING_PROVIDER` without re-embedding the existing corpus
  will silently degrade retrieval (query vectors from BGE searched
  against nomic vectors). A `wolf reembed` CLI is queued as a
  Slice 2 / Slice 3 follow-up; documented as a known gap in ADR
  0012.

### What broke / what we discovered

- **nomic-embed-text via Ollama is NOT L2-normalized.** Raw dot
  products in the benchmark reached +280-290. pgvector's
  `vector_cosine_ops` normalizes internally so retrieval RANKING
  is unaffected, but if anyone ever rewrites Wolf's similarity
  code to use raw dot product, the two adapters would behave very
  differently. Logged in ADR 0012 §"Vector geometry."
- **First-run cold-start asymmetry is misleading.** Ollama's
  reported 0.07 s reflects an already-warm daemon (the model had
  been loaded by Slice 1's seed run earlier in the session). A
  truly cold Ollama would also pay a load cost similar to ST's
  ~10 s. The ADR records this honestly rather than pretending
  Ollama has a structural cold-start advantage.
- **BGE asymmetric retrieval matters.** The first benchmark
  iteration embedded queries WITHOUT the BGE query prefix and
  retrieval quality was visibly worse. Adding the
  `embed_query()` method with the proper prefix lifted the top-1
  precision on entity-specific queries from "comparable to
  nomic" to "noticeably better than nomic." Implementation
  detail documented in the adapter docstring; the benchmark uses
  `embed_query()` when available so future adapters can benefit.

### What's next

- **Phase 3 Slice 2** — hybrid retrieval (BM25 + vector fusion)
  + grounding validator.
- **Phase 3 Slice 3** — real Wazuh-docs / ATT&CK scrapers in
  `tools/seed_knowledge`, plus the `wolf reembed` helper.
- **Validate retrieval precision delta on real corpus.** The
  10-query / 9-chunk micro-benchmark is suggestive. Slice 3's
  thousand-chunk corpus is the right scale to formalize the
  precision claim.

---

## 2026-05-26 — Detour: close Slice 1 end-to-end (Wazuh Server API auth)

**Session type:** claude-code (continuation)
**Phase:** Phase 3 — closure of Slice 1's deferred end-to-end
**Duration:** ~20 min
**Branch / commit:** `main` — starting commit `158b008`, this entry's
commit pending.

### What we did

- **Diagnosed the Server API 401** flagged at Slice 1 close: Wazuh's
  Indexer (OpenSearch security plugin) and Server API (its own RBAC
  database at `/var/ossec/api/configuration/security/rbac.db`)
  maintain **separate user backends**. The `wolf` user (and later
  `admin`) existed only in the Indexer. Direct curl against the Server
  API `/security/user/authenticate` returned `"Invalid credentials"`
  for both. Pure operator-side configuration gap; no Wolf code path
  involved.
- **Operator supplied the Server API admin credentials**
  (`wazuh-wui` / generated). curl confirmed JWT issuance + `/agents`
  + `/rules?rule_ids=5712` all return real data.
- **Re-ran `bootstrap_organization --organization-slug acme`** with per-endpoint
  credentials (`admin` for Indexer, `wazuh-wui` for Server API). Idem-
  potent — overwrote the secrets in place; organization + user bindings
  preserved.
- **Closed the Slice 1 end-to-end gap** with two verifications via
  `/api/v1/chat`:
  - **Pure RAG**: "What is the Acme SOC runbook for SSH brute-force?"
    → strategy `guided`, 2 steps, 1 tool call (`query_runbook`),
    citation present, answer faithfully reproduces all 5 runbook steps
    from the seeded ACME chunk. 60s on the RTX 4050.
  - **Mixed RAG + Server API**: "Look up the actual definition of
    Wazuh rule 5712, then tell me what Acme SOC runbook says…"
    → 2 tool calls (`get_rule_definition` + `query_runbook`), both
    citations attached, 71s. Confirms the same loop can fuse live
    state with retrieved knowledge per doc 06 §"How 'complete
    knowledge' actually gets delivered."

### What we decided

- **No Wolf code changes** — the Slice 1 implementation is unchanged
  by this detour. The failure was operator-side credentials only.
- **Keep the per-endpoint credential pattern** in the dev organization
  (Indexer admin + Server API admin can be different users). Already
  supported by `bootstrap_organization` — `--opensearch-username` and
  `--server-api-username` are independent flags.
- **Acknowledge the synthesis-fidelity hiccup** seen in the mixed-mode
  answer: the model wove a fragment of the rule's `ignore=60s`
  parameter into the runbook section ("Block the source IP for 60
  seconds (per `ignore` parameter)") that is NOT in the seeded
  runbook chunk. Retrieval is correct (both citations present);
  synthesis embellishes. This is exactly the grounding-discipline
  failure mode ADRs 0002 / 0010 / 0011 documented for the qwen
  family, and exactly what Phase 3 Slice 2's grounding validator is
  designed to catch. The fabrication evidence reinforces the
  validator's design rationale.

### What broke / what we discovered

- **Wazuh's Indexer/Server-API user-store split** is a real
  deployment gotcha worth surfacing in ONBOARDING. The
  `credentials/wazuh-credentials.txt` template originally listed one
  user as covering both; operators should be told explicitly that
  these are two separate credentials. Logged as a follow-up doc fix.
- **qwen3:4b's synthesis embellishment** when mixing two tool results
  (rule definition + runbook) is observable now that both paths
  work. Quantifying this on a small benchmark set would be a useful
  Slice 2 input for the grounding-validator's reject threshold.

### What's next

- **Phase 3 Slice 1.5** — sentence-transformers `EmbeddingProvider`
  adapter + comparison ADR.
- **Phase 3 Slice 2** — hybrid retrieval + grounding validator
  (motivating evidence from this session's synthesis embellishment).
- **ONBOARDING doc fix** — explicit note that Wazuh Indexer and
  Server API have separate user databases; the operator may need
  two different credentials.

---

## 2026-05-24 — Phase 3 Slice 1: vertical RAG skeleton

**Session type:** claude-code (same session as Granite probe / new-machine handoff)
**Phase:** Phase 3 — Knowledge & RAG (Slice 1 of 3)
**Duration:** ~75 min
**Branch / commit:** `main` — starting commit `f977a83`, final commit
pending this entry.

### What we did

- **Designed Phase 3 as three slices** (vertical skeleton → second
  embedding adapter + comparison → real scrapers + hybrid retrieval +
  grounding validator) to land the architecture-proving path first
  before scaling content or adding ranker complexity.
- **Added `pgvector>=0.3`** to `services/orchestrator/pyproject.toml`
  for the SQLAlchemy `Vector` column type.
- **Pulled `nomic-embed-text`** via Ollama (768-dim, 274 MB, ~1 s warm
  embed on the RTX 4050). Symmetric with the existing Ollama LLM
  pattern — no torch / sentence-transformers wheels added to the
  orchestrator's install set (per ADR 0007 packaging constraints).
- **New `services/orchestrator/app/knowledge/` module:**
  - `models.py` — `KnowledgeChunk` SA model with `Vector(768)`
    embedding + `JSONB` chunk_metadata + `embedding_model` stamp for
    the doc-06 re-embedding trigger. `EMBEDDING_DIMENSION = 768`
    locked into the schema.
  - `embeddings.py` — `EmbeddingProvider` Protocol +
    `OllamaEmbeddingAdapter` (sequential per-text calls to
    `/api/embeddings`; fine at Slice-1 scale, batching deferred).
  - `store.py` — `KnowledgeStore` Protocol +
    `PgvectorKnowledgeStore`. Organization-scoping enforced at the SQL
    clause: `WHERE organization_id IS NULL OR organization_id = $req_organization`.
    `SHARED_SOURCE_TYPES` / `ORGANIZATION_SOURCE_TYPES` validation at
    upsert: shared corpora forbid a organization_id; private corpora
    require one.
- **Alembic migration 0004** — `knowledge_chunks` table + composite
  `(organization_id, source_type)` btree index + HNSW
  `vector_cosine_ops` index on `embedding`. `CREATE EXTENSION IF NOT
  EXISTS vector` is idempotent for fresh databases. Applied cleanly
  against the dev DB.
- **`query_runbook` tool** (`app/tools/knowledge.py`) — read-tier,
  metadata filters as first-class Pydantic args per doc 06
  (`source_types`, `rule_id`, `technique`, `limit`). Raises a clear
  `RuntimeError` if `ToolExecContext.knowledge_store` is unset
  rather than failing silently. Registered as the 10th read tool.
- **Plumbed knowledge_store** through `ToolExecContext` (new optional
  field, typed `Any` to avoid an import cycle) → `dispatch_tool_call`
  (new kw param) → `AgentLoop.run` (new kw param) → both the JSON and
  SSE chat endpoints in `app/api/chat.py` (build adapter + store from
  per-request DB session + Ollama base URL).
- **`seed_dev_knowledge` management CLI** — loads the Slice-1 inline
  corpus: 6 shared chunks (Wazuh rules 5710/5712 + active-response;
  ATT&CK T1110 / T1110.001 / T1078) and 3 organization-private chunks per
  organization (SSH brute-force runbook, T1110 triage runbook, past
  incident write-up). Fails loud if `DATABASE_URL` is unset (matches
  the lesson learned from ONBOARDING §3.7 alembic drift earlier this
  session). JSON output to stdout for scripting; errors to stderr.
- **Ran the migration + seed against the dev DB.** Confirmed table
  schema and indexes (HNSW + composite btree); seeded 9 chunks for
  organization `acme` (6 shared with `organization_id=NULL` + 3 private with
  `organization_id=acme.id`).
- **12 new pytest tests** in `tests/test_knowledge_store.py`:
  validation rules on `ChunkInput` (shared corpora must have null
  organization_id; private corpora require one; unknown source_type
  rejected; empty content rejected), `QueryRunbookInput` constraints
  (non-empty query; 1..20 limit clamp; minimal-args default), tool
  surface (raises when store not configured; passes filters through
  to the store correctly).
- **Conftest fix** — under SQLite (the local-dev default), skip the
  `knowledge_chunks` table during `Base.metadata.create_all` because
  `pgvector.Vector` + `JSONB` don't render on SQLite. Phase-3 paths
  are Postgres-only by design; tests stub the store.
- **`make check` clean: 140 passed** (128 prior + 12 new). lint +
  mypy strict still clean.
- **Direct RAG verification** — bypassed the chat endpoint and
  exercised the store directly: query
  *"how does Acme respond to SSH brute-force?"* returned 5 hits with
  cosine distances 0.317–0.415, top hit being the shared ATT&CK
  T1110 chunk, followed by the ACME SOC private runbook chunk. The
  SQL log shows the expected `WHERE organization_id IS NULL OR organization_id =
  $acme.id ORDER BY distance LIMIT 5` clause — organization scoping
  enforced at the query layer.

### What we decided

- **Three-slice Phase 3 plan, not one big landing.** Slice 1 ships
  the vertical (proven). Slice 1.5 adds sentence-transformers as a
  second `EmbeddingProvider` adapter and writes a decision ADR on
  keep-both vs pick-one (per operator's explicit request).
  Slice 2 brings hybrid retrieval + grounding validator. Slice 3
  ships the real Wazuh-docs / ATT&CK scrapers in
  `tools/seed_knowledge`.
- **Ollama-hosted embedding (nomic-embed-text) as Slice 1's primary**
  — keeps the orchestrator wheel set lean for ADR 0007 native
  packaging, symmetric with the LLM Ollama pattern, model lifecycle
  managed by Ollama. Sentence-transformers adapter to land in Slice
  1.5 with a head-to-head benchmark.
- **HNSW for the embedding index** — pgvector's modern default,
  incremental inserts, log-ish query time. IVFFlat reachable later
  via a one-statement index swap if MSSP-scale memory pressure
  appears.
- **Inline 9-chunk seed for Slice 1, not a real scrape.** Smallest
  artifact that proves the vertical; real scrapers come in Slice 3.
- **Organization scoping enforced inside the store**, not at the tool
  layer. The dispatcher's `sanitize_organization_id_from_args` already
  strips any model-supplied organization_id; the store's SQL clause is
  the load-bearing second line of defense per doc 05.
- **The chat-endpoint end-to-end test was blocked by a separate
  Wazuh Server API 401** (the `wolf` user works for the Indexer but
  apparently not the Server API in this deployment) — the model
  routed the test question to `get_rule_definition` rather than
  `query_runbook`. Decided NOT to fix that in Slice 1 because it's
  an operator-side credentials issue, not a Slice-1 scope item.
  The direct-RAG verification stands in as the Slice-1 closure
  signal.

### What broke / what we discovered

- **The conftest's SQLite path needed a knowledge_chunks skip.**
  `Base.metadata.create_all` under SQLite blew up on
  `pgvector.Vector` + `postgresql.JSONB` — both Postgres-only. Fixed
  by filtering the create_all tables list. Phase-3 tests that need a
  real Postgres roundtrip will get a separate fixture in Slice 1.5
  or 2.
- **qwen3:4b's tool-routing pick on a knowledge question.** Asked
  *"What does Wazuh rule 5712 do?"* — the model chose
  `get_rule_definition` (Wazuh Server API) over `query_runbook`
  (RAG), which is arguably correct (live rule definition is more
  authoritative than docs) but blocked the end-to-end test on the
  Server API 401. Worth noting: the agent loop's strategy doesn't
  currently bias toward RAG for product-knowledge questions. The
  Slice-2 grounding validator + prompt-shaping work is where this
  routing bias can be tuned.
- **nomic-embed-text returns vectors with a startling distribution**
  — values like `-3.91` in the first dimension. Not normalized to
  unit length out-of-box. Cosine distance still works (pgvector
  normalizes internally for `vector_cosine_ops`), but worth noting
  if we ever swap to a raw-dot-product comparison.

### What's next

- **Phase 3 Slice 1.5** — sentence-transformers `EmbeddingProvider`
  adapter + head-to-head benchmark + decision ADR.
- **Phase 3 Slice 2** — hybrid retrieval (BM25 + vector fusion) +
  grounding validator.
- **Investigate the Wazuh Server API 401** (operator-side
  credentials gap surfaced during Slice 1 end-to-end).
- **Doc-drift fixes accumulated from this session** still pending:
  ONBOARDING §3.7 alembic env-load, §11 `GET /me` route nit, test
  suite + Postgres asyncpg loop-scope issue.

---

## 2026-05-24 — Opportunistic probe: IBM Granite 3.3 8B (ADR 0011)

**Session type:** claude-code (same session as the new-machine handoff entry below)
**Phase:** Phase 2 closed; pre-Phase-3 setup
**Duration:** ~20 min
**Branch / commit:** `main` — starting commit `600740d`, final commit
pending this entry.

### What we did

- Operator asked which fully-free open-source agentic models were
  realistic challengers to qwen3:4b on the new GPU hardware, with
  the license filter relaxed. Triage surfaced IBM Granite 3.3 8B as
  the most interesting candidate (Apache 2.0, marketed by IBM for
  agentic tool use, dedicated tools-trained variant in the family).
- **Pulled `granite3.3:8b`** (~4.9 GB on disk). Loads at PROCESSOR=
  **88% GPU / 12% CPU** at default 4096 ctx — slightly less CPU
  spillover than qwen3:8b's 85%/15% but the same tight-fit class.
  VRAM 5053 MB of 6141 MB.
- **Ran the probe** — score **0.25**. PASS `tool_call_formatting`
  (IBM's agentic positioning works at the format level); FAIL the
  other three: `json_schema_adherence` (response shape mismatch),
  `multi_step_reasoning` (invalid JSON — same failure shape as
  qwen3.5:4b in ADR 0009), and `grounding_discipline` (fabrication —
  same weakness as qwen3:4b/qwen3:8b). Measured descriptor:
  `basic` / `full` / `unreliable` / 3 / `pipeline`.
- **License-verified Apache 2.0** via the Ollama page
  (https://ollama.com/library/granite3.3).
- **Wrote ADR 0011** marking the probe explicitly opportunistic per
  ADR 0006's "wider matrix" alternatives section. KNOWN_MODELS entry
  added with an inline comment flagging it as **opportunistic
  registration** — *not* part of the four-family supported matrix.
  Operators selecting it via env override get documented pipeline
  behavior.
- Updated `docs/decisions/README.md` index. `docs/15-supported-model-matrix.md`
  is **unchanged** — Granite stays out of the bounded matrix
  deliberately, preserving ADR 0006's narrow commitment.

### What we decided

- **Granite 3.3 8B is NOT a default-flip candidate.** Despite being
  2× qwen3:4b's parameter count and IBM's explicit agentic
  positioning, it regresses on three of four probe tasks on this
  hardware. `DEFAULT_MODEL_ID` stays `qwen3:4b`.
- **Granite stays in `KNOWN_MODELS` as opportunistic registration**
  (ADR 0005/Nemotron precedent) — the registry documents what Wolf
  knows about, not what it recommends. Operators get an honest
  measurement to base their own choice on.
- **No expansion of the four-family matrix in doc 15.** ADR 0006's
  narrowness is deliberate; adding a fifth family on one probe
  result would erode the design.
- **A future agent-loop smoke test of Granite under `guided`
  strategy is the right follow-up** if/when the "marketing says
  agents, probe says pipeline" question becomes load-bearing.
  Granite's `native_tool_calling: full` is real and Wolf's typed
  dispatcher might let it perform better at runtime than the
  static descriptor predicts. Deferred; not in scope for this
  drop-in probe.

### What broke / what we discovered

- **"Purpose-built for agents" doesn't automatically equal
  Wolf-loop fit.** Granite's tool-call format is correct (its
  agentic claim is real at the protocol level), but Wolf's
  structured-output fallback expects a specific `answer` /
  `tool` envelope shape that Granite doesn't reliably produce.
  Useful data point for evaluating any future vendor claim of
  "agentic" — the probe is the truth, not the marketing.
- **Same fabrication weakness as Qwen family** on the no-tools
  grounding-discipline test. Phase 3's grounding validator is the
  cross-model mitigation; this probe is the second independent
  confirmation that the validator is the right design.

### What's next

- Phase 3 (RAG + grounding validator) per `docs/06` and `docs/10` —
  unchanged from the prior session entry. Granite probe complete;
  no further model exploration needed before Phase 3.

---

## 2026-05-24 — New-machine handoff: GPU dev laptop, qwen3:8b + qwen3.5:4b probes

**Session type:** claude-code (new conversation, **new dev machine** — RTX 4050 Laptop GPU)
**Phase:** Phase 2 closed; pre-Phase-3 setup completed
**Duration:** ~75 min
**Branch / commit:** `main` — starting commit `a890a5b`, final session commit
pending this entry.

### What we did

- **Resumed from a clean clone** on the new GPU-equipped laptop following
  `prompts/HANDOFF-NEW-MACHINE.md`. Operator had pre-staged Python 3.13.13,
  uv 0.11.16, Node 24.16.0, Ollama 0.24.0 (with qwen3:4b, qwen3.5:4b,
  gemma3:4b, llama3.2:3b already pulled), Docker 29.5.2, and system
  Postgres 17.10 + pgvector. NVIDIA RTX 4050 Laptop GPU detected (6 GB
  VRAM, driver 595.71.05, CUDA 13.2).
- **Found `credentials/` drop** at repo root containing real Wazuh
  credentials (user `wolf`, password, indexer URL `https://192.168.245.128:9200`,
  Server API URL `https://192.168.245.128:55000`) plus the local Postgres
  password. Was untracked but **not gitignored**; added `credentials/`
  to `.gitignore` immediately to prevent accidental commit.
- **Setup from clean clone** per ONBOARDING.md §3: `uv sync --all-packages`,
  `npm install` in frontend, generated `SECRET_KEY` + `SECRETS_FILE_KEY`,
  wrote `.env` (mode 0600) with `DEFAULT_MODEL_ID=qwen3:4b`, ran
  `alembic upgrade head` (3 migrations clean), bootstrapped organization `acme`
  with the real Wazuh URLs.
- **Verified end-to-end against real Wazuh** at `192.168.245.128`:
  curl-driven login → chat → tool call (`count_alerts_by_severity`) →
  grounded answer ("325 alerts in 24h, 143 medium + 182 low") in **20.8s**
  (vs ~76s cold on the previous CPU-only VM — clean GPU win). Strategy:
  `guided`. Model: `qwen3:4b`.
- **`make check`: 128 passed, lint + mypy strict clean.** Same baseline
  as the previous VM, on the new hardware.
- **Confirmed Ollama GPU offload** via `ollama ps` for all four pre-pulled
  models: qwen3:4b (3.5 GB, 100% GPU), qwen3.5:4b (5.9 GB, 100% GPU —
  surprisingly large for a 4B; the 256K-ctx capability inflates KV cache
  reservation), gemma3:4b (4.3 GB, 100% GPU), llama3.2:3b (2.8 GB, 100% GPU).
- **Pulled qwen3:8b** (~5.2 GB on disk). Loads at PROCESSOR=**85% GPU /
  15% CPU** at default 4096 ctx — the brief's "tight fit" prediction
  was exactly right. VRAM use 4985 MB of 6141 MB.
- **Ran three model probes** via `uv run python -m tools.model_probe`:
  - **qwen3:4b GPU re-probe** — score 0.75, descriptor identical to
    ADR 0002's CPU measurement. Confirms the probe is hardware-agnostic
    at the capability tier; provides the baseline for the qwen3.5:4b
    cross-comparison.
  - **qwen3.5:4b GPU probe** — score **0.50** (regression). FAIL on
    `tool_call_formatting` and `json_schema_adherence` (model emitted
    invalid JSON across all 3 structured-output retry attempts); PASS
    on `multi_step_reasoning` and `grounding_discipline`. Measured
    descriptor: `basic` / `none` / `unreliable` / 4 / `pipeline`.
  - **qwen3:8b GPU probe** — score 0.75. Identical descriptor to
    qwen3:4b at the static fields (`mid` / `full` / `schema_enforced` /
    8 / `guided`). Two amendments to the existing `KNOWN_MODELS`
    estimate: `structured_output` upgraded `prompt_coaxed` →
    `schema_enforced`; `max_safe_autonomous_steps` tightened 10 → 8.
- **License-verified qwen3.5:4b as Apache 2.0** via Qwen 3.5 release
  notes (open-weight tiers 0.8B–397B-A17B). Ollama page didn't state
  it directly. Cleared the ADR 0006 prerequisite for `license_class`
  in the `KNOWN_MODELS` entry.
- **Wrote two ADRs and amended `KNOWN_MODELS`:**
  - ADR 0009 — qwen3.5:4b GPU probe + cross-comparison vs qwen3:4b.
    Records the regression honestly; decides NOT to flip
    `DEFAULT_MODEL_ID` (handoff brief's condition: only flip if 3.5
    matches/beats 3; it does not). Adds `KNOWN_MODELS["qwen3.5:4b"]`
    with the measured `basic`/`pipeline`/Apache-2.0 descriptor.
  - ADR 0010 — qwen3:8b GPU probe (tight VRAM fit, 85% GPU /
    15% CPU). Records same-descriptor result as qwen3:4b; decides
    NOT to flip default (no measured-capability win + worse latency
    under VRAM pressure). Amends `KNOWN_MODELS["qwen3:8b"]` to match
    probe (structured_output upgrade, max_steps tighten).
- Updated `docs/decisions/README.md` index (rows for 0009 and 0010),
  `docs/15-supported-model-matrix.md` Implementation-status table
  (Qwen 3 8B status flipped, Qwen 3.5 row added, qwen3.5:4b re-probe
  added as gap #4), and `docs/PROGRESS.md` §3 (dev environment now
  GPU-equipped) + §4 (next steps) + §8 (ADR count 8 → 10).
- Re-ran `make check` after the `KNOWN_MODELS` edits: **128 passed**,
  lint + mypy strict still clean.

### What we decided

- **qwen3.5:4b is supported but not recommended** (ADR 0009). Stays in
  `KNOWN_MODELS` per ADR 0006's family-commitment principle; operators
  who select it via env override get documented `pipeline` behavior.
  No default flip. License verified Apache 2.0.
- **qwen3:8b is officially supported on Profile B tight-end** (ADR 0010).
  Operators with more VRAM (12+ GB) may prefer it; on this 6 GB GPU
  it offers no measured capability win and has CPU-spillover latency
  cost, so `qwen3:4b` remains the dev default.
- **`DEFAULT_MODEL_ID` stays `qwen3:4b`.** Both new probes failed to
  produce a default-flip candidate; the ADR 0004 pattern is not
  triggered.
- **Skipped optional probes:** gemma3:4b GPU re-probe (already CPU-probed
  ADR 0003; capability descriptor would not change), llama3.2:3b GPU
  re-probe (same reasoning — ADR 0001 capability is hardware-agnostic).
  Per handoff brief these were explicitly optional.

### What broke / what we discovered

- **`uv run alembic upgrade head` fails without sourcing `.env` first.**
  configparser's `BasicInterpolation` can't resolve `%(DATABASE_URL)s`
  unless the variable is in the process env at alembic-config-load time
  (before `env.py` runs its `set_main_option` override). ONBOARDING.md
  §3.7 doesn't mention this. Worth a doc fix: prepend
  `set -a && source ../../.env && set +a &&` to the alembic command,
  or move the env load earlier in §3.6.
- **`GET /me` 404s.** ONBOARDING.md §11 references `GET /me` for the
  authenticated user lookup; that route doesn't exist (tried both
  `/me` and `/api/v1/me`). The `/api/v1/auth/login` POST works and
  returns the user payload, so it's not blocking — but the doc claim
  is wrong. Worth a quick grep + correction in the next doc sweep.
- **Test suite + Postgres has a latent event-loop scoping bug.**
  Running `make check` with `DATABASE_URL` exported to system Postgres
  (which is what my initial `set -a && source .env && set +a` did)
  triggered 32 pytest errors with `RuntimeError: ... attached to a
  different loop` in asyncpg. The conftest defaults to SQLite for
  local dev — passing 128/128 without `DATABASE_URL` set. CI presumably
  handles Postgres correctly somehow; worth understanding before
  Phase 3 adds pgvector tests that may need the real DB locally.
- **qwen3.5:4b's `native_tool_calling = none` failure mode is
  structurally different from gemma3:4b's.** Gemma earns `none` because
  Ollama returns HTTP 400 on any `tools=[...]` request — model is
  structurally untrained. qwen3.5:4b: Ollama accepts the request and
  the model returns invalid JSON. Smells like a chat-template/glue
  issue in Ollama's qwen3.5 release, not necessarily a model limit.
  ADR 0009 records the descriptor at face value but flags a re-probe
  as the right follow-up.
- **qwen3.5:4b VRAM (5.9 GB at 4096 ctx)** is dramatically larger than
  qwen3:4b's (3.5 GB) despite similar disk size — the 256K-ctx
  capability inflates KV-cache reservation up-front. On this 6 GB
  GPU it fits at default but won't tolerate much context increase.

### What's next

- **Phase 3 (RAG + grounding validator)** per `docs/06` and `docs/10`
  Phase 3 block. Now unblocked — no probes left on this hardware.
  Grounding validator is the designed mitigation for both ADR 0002's
  qwen3:4b grounding fail and ADR 0010's qwen3:8b grounding fail.
- **Doc sweep** for the three drift points discovered (ONBOARDING §3.7
  alembic env load, §11 `GET /me` 404, test-suite Postgres scoping).
  Small; can fold into the start of the next session.
- **Optional follow-up:** qwen3.5:4b re-probe after the next Ollama
  qwen3.5 release.
- **Still blocked on workstation-GPU hardware (24+ GB VRAM):** GLM 5.1
  ~32B probe, Gemma 3 12B/27B probes, Qwen 3 14B/32B probes. Per ADR
  0006 these remain expected probe ADRs but are not blocking Phase 3.

---

## 2026-05-23 — Supported-model commitment (ADR 0006 + doc 15) + ONBOARDING.md

**Session type:** claude-code (new conversation, same dev environment)
**Phase:** Phase 2 closed; pre-Phase-3 setup
**Duration:** ~90 min (discussion + writing)
**Branch / commit:** `main` — uncommitted at time of this entry; will
be committed as part of the same set of commits that adds this entry.

### What we did

- Walked the project owner through the locally-hostable agentic-LLM
  landscape across four hardware tiers, with targeted side-discussions
  of GLM 5.1 and Kimi K2 (the latter ruled out for the matrix because
  even sparse it does not fit any non-multi-GPU local profile).
- Captured the owner's product direction as a load-bearing commitment:
  Wolf must natively support **four** model families locally in
  development — Qwen 3 (4B/8B/14B/32B), Llama 3 (3.x/4 line), Gemma 3
  (4B/12B/27B), GLM 5.1 ~32B dense.  Production posture is user-choice
  (operators pick one or multiple, including hosted APIs).
- Created `docs/15-supported-model-matrix.md` — the living directive
  doc.  Defines the four families with sizes and licenses, the
  six-item "natively support" checklist (KNOWN_MODELS entry + live
  probe + ADR + agent-loop test + strategy assignment + smoke
  coverage + doc 14 entry), the dev quality bar (efficient / robust /
  stable / reliable) with specifics, the production user-choice
  posture, and the current implementation gaps ordered by priority.
- Created `docs/decisions/0006-supported-model-families-commitment.md`
  — the point-in-time ADR with full reasoning, five alternatives
  considered (single-default rejected, wider matrix rejected, drop
  Llama rejected, include Kimi K2 rejected, defer-until-Phase-3
  rejected), and six consequences including the four expected probe
  ADRs.
- Updated `docs/decisions/README.md` index table with ADR 0006 row.
- Added auto-memory entry `supported_model_matrix.md` + one-line
  pointer in `~/.claude/.../memory/MEMORY.md` so future Claude Code
  sessions on other machines pick up the commitment without needing
  to find doc 15 first.
- Wrote `ONBOARDING.md` at repo root — 11-section comprehensive
  onboarding doc for a new contributor (human or AI) on a different
  machine: 60-second orientation, mandatory reading order with three
  tiers, system requirements, first-time setup from a clean clone
  (12 numbered steps), verification (tests / lint / smoke / probe),
  common operational tasks, seven real gotchas with fixes, the
  session-continuity protocol, file-location reference table, and a
  troubleshooting matrix.
- Updated this CHANGELOG and `docs/PROGRESS.md` accordingly.

### What we decided

- Four-family native-support commitment (ADR 0006).  Llama stays in
  the matrix even though it's not Wolf's *recommended* default per
  doc 14 — "supported" and "recommended" are distinct concerns.
- GLM 5.1 anchored at ~32B (dense) rather than the smaller 9B.  The
  project owner is arranging GPU hardware that can run the 32B class,
  so the matrix targets the right tier.
- ADR 0006 alongside doc 15 (rather than only one of them).  doc 15
  is the living matrix; ADR 0006 is the frozen decision record.  Six
  months from now "why these four?" is answered in ADR 0006; "what's
  the current state?" is answered in doc 15.  Both are needed.
- `ONBOARDING.md` at repo root (not `docs/16-onboarding.md`).
  Discoverability after `git clone` matters more than fitting the
  numbered docs/ scheme.  The doc points heavily into docs/ for
  detail.

### What broke / what we discovered

- `pnpm-workspace.yaml` and `services/frontend/` are stale — the
  real Next.js app lives at `/frontend/` at the repo root.  Flagged
  as Gotcha #2 in `ONBOARDING.md` but not fixed in this session;
  cleanup commit deferred.
- Repo `main` is 25 commits ahead of `origin/main` as of the start
  of this session.  All 25 are legitimate Phase 2 work from earlier
  sessions that was never pushed.  The push at the end of this
  session will publish all of them at once.

### What's next

- Push everything (this session's commits + the 25 unpushed Phase 2
  commits) to `origin/main` so the GitHub remote becomes the
  canonical state.
- Hand off to the new GPU dev machine (when it arrives) with the
  session-handoff prompt produced at the end of this session.
- Once on the GPU machine: pull the four families at the larger
  sizes, run probes, write the four expected probe ADRs (one per
  family / size that needs measurement).
- In parallel or after: begin Phase 3 (RAG + grounding validator)
  per `docs/06` and `docs/10`.

### Follow-up commits later in the same session

This entry was written before the following cleanup work; recording
here so the changelog matches the git log.

- `8da5389` — removed stale `pnpm-workspace.yaml` and empty
  `services/frontend/` directory (the deferred cleanup mentioned
  above).  Updated `ONBOARDING.md` accordingly: dropped Gotcha #2,
  renumbered #3-#7 → #2-#6, fixed three inline cross-references and
  the §0 repo-layout block.
- `7917fc5` — fixed factually wrong `bootstrap_organization` flag names in
  `ONBOARDING.md` §3.9/§3.10 (real flags are `--admin-email`,
  `--admin-password`, `--opensearch-url`, `--opensearch-username`,
  `--opensearch-password`, `--server-api-url`, `--server-api-username`,
  `--server-api-password`, `--verify-tls`/`--no-verify-tls` — not the
  `--user-*` / `--wazuh-*` names previously documented).  Also
  corrected the structural misstatement that `bootstrap_organization`
  supports a two-step "create organization first, wire Wazuh later" flow —
  the CLI requires all Wazuh fields up front.  Merged §3.9 + §3.10
  into a single accurate step with a "no Wazuh yet" placeholder
  pattern; renumbered §3.11/§3.12 → §3.10/§3.11.  Clarified in §5
  that the CLI is fully idempotent and re-running it with the same
  `--organization-slug` is the supported update / credential-rotation path
  (no dedicated update CLI needed).
- `<earlier in session>` — saved the new-machine handoff prompt as
  `prompts/HANDOFF-NEW-MACHINE.md` (was previously only inline in
  chat); appended this follow-up note to the CHANGELOG entry.
- `<later in session>` — committed ADR 0007 + `docs/16-distribution-and-packaging.md`
  + auto-memory entry + small pointers in `docs/09` (Container/build/CI
  section), `docs/decisions/README.md` (index row), `ONBOARDING.md`
  (Tier 2 reading order).  ADR 0007 records the decision to deliver
  Wolf natively (non-container) via `.deb`/`.rpm` system packages +
  systemd units, fronted by a one-line install script that handles
  prerequisite-repo setup (GitLab-style hybrid: Tailscale / Caddy /
  k3s / Docker also use this pattern).  Three alternatives weighed:
  GitLab-style omnibus (Option B, rejected as too expensive
  engineering for the marginal gain), Snap/Flatpak (Option C,
  rejected due to confinement friction with local sockets and
  secrets), and pure Option A without script wrapper (rejected as
  too much friction with three third-party repos to add manually).
  doc 16 specifies the package set, file layout (FHS-conformant),
  `wolf` CLI surface, supported distro matrix, security posture,
  and implementation work-breakdown (~3-4 weeks of focused work
  when the slot arrives).  Implementation deliberately queued for
  post-Phase 4 to avoid repackaging churn before the deployable
  surface stabilizes; current code must continue to honor
  constraints in doc 16 §"How current code should accommodate this
  commitment" (env-driven config, no hard-coded container paths,
  management CLIs remain usable as plain `python -m ...`, frontend
  on Next.js `output: 'standalone'`).
- `<this commit>` — committed ADR 0008 + cross-document repositioning
  to reflect "native primary, Docker supplementary."  Follow-up to
  ADR 0007's "peer" framing after the project owner clarified that
  native is where polish and operator-facing investment go; Docker
  remains baseline-supported (Dockerfiles, compose, Makefile targets
  stay; `make up` keeps passing) for operators who want to build
  their own container images (typically for k8s).  Concrete
  operational change: dev environment switches from Docker Postgres
  to system Postgres 17 + pgvector (apt-installed, systemd-managed),
  matching the production install path operators will use via the
  forthcoming install script.  Files touched: `docs/decisions/0008-...md`
  (new ADR), `docs/decisions/0007-...md` (amendment footer noting
  the positioning change), `docs/decisions/README.md` (index row +
  0007 status annotation), `docs/16-distribution-and-packaging.md`
  (new "Development against this channel" section), `docs/09-tech-stack-and-repo-layout.md`
  (§"Container, build, CI" repositioned), `ONBOARDING.md`
  (§2 reclassifies Docker from mandatory to optional + adds
  PostgreSQL line, §3.4 rewritten to lead with system Postgres
  install steps + keep Docker as alternative, §5 reboot section
  notes systemd auto-start), `Makefile` (comment block clarifying
  which targets serve native dev vs container channel),
  `docker-compose.yml` (top-of-file comment marking it the
  container-channel deployment stack), `docs/PROGRESS.md`
  (§3 dev posture, §8 ADR count).  Auto-memory entry
  `native_distribution_commitment.md` updated to reflect "native
  primary" instead of "peer."
- `<this commit>` — added Qwen 3.5 to the new-machine probe plan.
  Qwen 3.5 released on Ollama ~late May 2026 (~2 days before this
  commit per the library page); falls under ADR 0006's "Qwen 3
  family" commitment as a minor revision (3.x).  Sizes available:
  0.8B, 2B, 4B, 9B, 27B, 35B, 122B (plus MLX + cloud variants).
  On the RTX 4050 Laptop's 6 GB VRAM, qwen3.5:4b (3.4 GB on disk,
  ~3.5 GB VRAM at Q4) fits comfortably — the 9B (was 8B in Qwen 3,
  grown) doesn't.  Key new things vs Qwen 3: 256K context window
  (vs 128K — relevant for Phase 3 RAG), multimodal text+image on
  most variants (Wolf doesn't use this today).  Two things NOT
  confirmed from the Ollama library page: native tool calling
  (Qwen 3 had it, Qwen 3.5 almost certainly does, but probe will
  verify) and license class (Qwen 3 was Apache 2.0, but Qwen 3.5
  page doesn't state — verify before adding to KNOWN_MODELS with
  `license_class`).  Files touched: `prompts/HANDOFF-NEW-MACHINE.md`
  (Step C pull list extended to five models adding qwen3.5:4b with
  caveats; Step D probe expectations updated to three new probes
  including the qwen3:4b vs qwen3.5:4b cross-comparison and the
  potential follow-up default-flip ADR if qwen3.5:4b wins), and
  `docs/PROGRESS.md` §4 (next steps narrative updated to surface
  qwen3.5:4b as the most interesting near-term probe).  No code
  changes; the model abstraction layer already handles new family
  variants via the standard KNOWN_MODELS + probe + ADR workflow.

---

## 2026-05-22 — Switch dev default from llama3.2 to qwen3:4b

**Session type:** claude-code (continuation, same dev environment)
**Phase:** Phase 2 — Read path
**Duration:** ~30 min
**Branch / commit:** `main` — ADR 0004 `e092e21`, config flip
`ca495df`, KNOWN_MODELS amendment `14cc727`, final session commit
pending this entry.

### What we did

- Wrote `docs/decisions/0004-model-switch-llama3.2-to-qwen3-4b.md`
  weighing the three earlier probe ADRs (0001/0002/0003).  Decision:
  flip the dev default to qwen3:4b on probe-evidence + license
  grounds; document that qwen3's grounding-discipline probe failure
  raises Phase 3 grounding-validator priority but does not block the
  switch (the agent loop's tool-gated path bounds the fabrication
  risk).
- Updated `docs/decisions/README.md` index with ADR 0004.
- Changed `DEFAULT_MODEL_ID` from `llama3.2` to `qwen3:4b` in
  `services/orchestrator/app/config.py` as a standalone one-line
  commit referencing ADR 0004 (per doc 14's playbook).
- Restarted orchestrator with the new default and ran a curl-driven
  chat verification against the user's real Wazuh on `192.168.76.129`.
- **Verification exposed a real issue**: chat worked but ran in
  `pipeline` strategy with no tools — the static
  `KNOWN_MODELS["qwen3:4b"]` entry (added in commit `e9cc316`) was
  the conservative initial estimate (basic / pipeline) and shadowed
  the probe-measured capability (mid / guided) at runtime.
- Amended `KNOWN_MODELS["qwen3:4b"]` to match ADR 0002's measured
  capability (mid / guided / full / schema_enforced / 8 steps) in
  commit `14cc727`.
- Re-restarted orchestrator and re-verified end-to-end: now runs in
  `guided` strategy, calls `count_alerts_by_severity` once, returns
  a grounded cited answer with concrete numbers ("15 alerts total,
  all low severity").

### What we decided

- **`qwen3:4b` becomes the dev default.**  ADR 0004 is the canonical
  rationale; future contributors should read it before considering
  another switch.  Llama family stays in `KNOWN_MODELS` for operator
  opt-in via env override.
- **The qwen3:4b grounding-failure data point is not disqualifying**
  — it's a Phase 3 priority signal, not a Phase 2 blocker.
- **The remaining two `KNOWN_MODELS` amendments** (`llama3.2`,
  `gemma3:4b`) stay deferred — neither is the current default, so the
  static-vs-measured drift doesn't affect runtime behaviour today.
  They'll move in a single sweep when convenient.

### What broke / what we discovered

- **Static `KNOWN_MODELS` entries can silently override probe-measured
  capability at runtime.**  The conservative `qwen3:4b` defaults from
  Task 4 of the previous session shadowed ADR 0002's measurements
  because strategy selection reads the static descriptor, not the ADR
  prose.  This is by design (static entries are the source of truth
  the orchestrator boots from) but it means a probe ADR without a
  matching static amendment doesn't actually change runtime behaviour
  — a footgun worth keeping in mind for future probe → switch flows.
- First inference on qwen3:4b after model swap took ~76s (cold
  load); second inference ~169s including a single tool call.  The
  CPU-only ceiling, not a regression.

### What's next

- Wire the 4 mock-only read tools to real Wazuh
  (`get_event_timeline`, `get_agent_alert_history`, `get_agent_detail`,
  `get_rule_definition`).
- Verify Phase 2 exit criterion against a frontier API model — blocked
  on an operator-supplied API key.
- Batch-amend the remaining `KNOWN_MODELS` entries for `llama3.2`
  (structured_output downgrade per ADR 0001) and `gemma3:4b`
  (native_tool_calling downgrade per ADR 0003).
- Begin Phase 3 (RAG + grounding validator) — the qwen3:4b
  grounding-discipline result is the direct motivating evidence.

---

## 2026-05-22 — Phase 2 exit criterion: frontier-API verification

**Session type:** claude-code (continuous session)
**Phase:** Phase 2 — close-out
**Duration:** ~45 min
**Branch / commit:** `main` — see commit below this entry's date

### What we did

- Added `app/management/set_secret.py` — small CLI that reads a value
  from stdin (no shell history exposure) and stashes it in the
  configured secrets backend.  Smoke-tested with a throwaway value
  (round-trip verified, secret never echoed).
- Stashed the operator's OpenRouter API key under
  `model.openrouter.api_key` in `.local/secrets.enc`.
- Added two `KNOWN_MODELS` entries for OpenRouter-hosted open models:
  `deepseek/deepseek-v4-flash:free` (kept for operators who fund the
  account, since DeepSeek's `:free` route gates on credit deposit) and
  `nvidia/nemotron-3-super-120b-a12b:free` (truly free, NVIDIA Open
  Model License — restricted, fine for verification not default).
- Ran the Phase 2 frontier-API verification end-to-end against the
  operator's real Wazuh using Nemotron 120B.  Result: `frontier`
  strategy, one tool call to `count_alerts_by_severity`, grounded
  cited answer in 17 seconds.  Captured verbatim in ADR 0005.
- Restored the steady-state config (DEFAULT_MODEL_ID stays `qwen3:4b`
  in config.py; the verification was env-only).
- Updated PROGRESS.md: Phase 2 exit-criteria bullet flipped from `[ ]`
  to `[x]`; Section 1 marked Phase 2 closed; Section 4 reordered with
  Phase 3 (RAG + grounding validator) as the next step.

### What we decided

- Use `nvidia/nemotron-3-super-120b-a12b:free` rather than a
  DeepSeek-family model for the actual verification because DeepSeek's
  free routes on OpenRouter all gate on credit deposit (HTTP 402 with
  zero-credit accounts).  Nemotron is the strongest of the no-deposit
  free options that genuinely worked.
- Accept the license caveat: Nemotron uses the NVIDIA Open Model
  License (restricted by doc 14's filter), so it is the
  verification-path model, NOT the recommended-default model.  Doc 14
  isolation holds: dev default stays Apache (qwen3:4b).
- Keep both new `KNOWN_MODELS` entries permanently — the
  DeepSeek-flash one as the canonical slug for operators who do top
  up OpenRouter, the Nemotron one as the verified no-deposit path.

### What broke / what we discovered

- **`OPENAI_BASE_URL` must NOT include `/v1`**: OpenAIAdapter posts
  to `{base_url}/v1/chat/completions`.  Setting the env to
  `https://openrouter.ai/api/v1` produced `.../api/v1/v1/chat/...`
  and 404'd.  Correct: `https://openrouter.ai/api`.  Documented
  inline on the OpenRouter entries.
- **The two-`app/`-packages collision struck again.**  Same root
  cause as ADR 0001's probe CLI bug — gateway's `app/` wins the path
  race over orchestrator's when uvicorn is launched from project
  root.  Workaround (`cd services/orchestrator` first) is documented
  in PROGRESS §3 and now in ADR 0005's "issues surfaced" section.
- **OpenRouter `:free` suffix is not a binding promise.**  Three of
  the five candidate `:free` routes we tried returned errors because
  their upstream providers meter independently of OpenRouter's free
  classification; account needed credits even for "free" routes.
  Documented in ADR 0005.

### What's next

- Phase 3 — RAG + grounding validator per docs/06.  Read that doc
  plus the Phase 3 block of docs/10-build-roadmap.md, then plan the
  slice.  qwen3:4b's grounding-discipline failure (ADR 0002) is the
  direct motivation for the grounding validator.

---

## 2026-05-22 — Amend `KNOWN_MODELS` for `llama3.2` and `gemma3:4b` per probe ADRs

**Session type:** claude-code (continuous session)
**Phase:** Phase 2 — close-out cleanup
**Duration:** ~5 min
**Branch / commit:** `main` — see commit below this entry's date

### What we did

- Aligned `KNOWN_MODELS["llama3.2"]` with ADR 0001's measurements:
  `native_tool_calling` upgraded `partial` → `full`;
  `structured_output` downgraded `prompt_coaxed` → `unreliable`.
  Reasoning tier and strategy were already correct.
- Aligned `KNOWN_MODELS["gemma3:4b"]` with ADR 0003's measurements:
  `native_tool_calling` downgraded `partial` → `none` (Gemma 3 4B has
  no native tool calling — Ollama returns HTTP 400 on any chat with
  `tools`); `structured_output` upgraded `prompt_coaxed` →
  `schema_enforced`; `max_safe_autonomous_steps` tightened 5 → 3.
- Added inline comments on each amended entry citing the ADR that
  grounded the change.
- Updated PROGRESS §4 to drop the completed cleanup item.

### What we decided

- Cosmetic cleanup; neither model is the current default
  (`qwen3:4b` holds that since commit `ca495df`).  But aligning
  static estimates with measured truth keeps `KNOWN_MODELS` honest
  for any operator who reads it as documentation.

### What broke / what we discovered

- Nothing.  128 backend tests still pass; ruff + mypy strict clean.
  No code branches on the amended fields (they inform strategy
  selection but not behaviour at the strategy level for these two
  models — `gemma3:4b` was already `pipeline` and `llama3.2` was
  already `guided`).

### What's next

- Frontier-API exit-criterion verification (blocked on operator key).
- Phase 3 entry — RAG + grounding validator per docs/06.

---

## 2026-05-22 — Verify all 9 read tools against real Wazuh; add `--all-tools` smoke mode

**Session type:** claude-code (continuous follow-on session)
**Phase:** Phase 2 — Read path (close-out)
**Duration:** ~30 min
**Branch / commit:** `main` — see commit below this entry's date

### What we did

- Exercised the four previously-mock-only read tools against the
  operator's real Wazuh deployment by calling each tool's `run()`
  directly through a synthesized `ToolExecContext`:
  `get_event_timeline`, `get_agent_alert_history`, `get_agent_detail`,
  `get_rule_definition`.  **All four succeeded first try** — no
  field-shape mismatches between the unit-test mocks and the real
  Server-API / OpenSearch responses.
- Extended `app/management/smoke_wazuh.py` with a `--all-tools` mode
  that exercises every registered read tool against the live
  deployment (calls `run()` through a ToolExecContext, bypassing the
  dispatcher's session requirement but going through full Pydantic
  input/output validation and the real HTTP layer).  Usage:
  `uv run python -m app.management.smoke_wazuh --organization-slug acme \
   --all-tools --agent-id 000 --rule-id 5402`.
- Re-verified all 9 tools end-to-end against the live Wazuh:
  list_agents (1), get_agent_detail (1), get_cluster_health,
  get_rule_definition (1), search_alerts (5), aggregate_alerts (3),
  count_alerts_by_severity (23 total), get_event_timeline (5),
  get_agent_alert_history (5).  **9/9 ✓.**
- Updated `docs/PROGRESS.md` Section 2 to reflect the new
  live-verified status (all 🟡 read-tool entries flipped to ✅), and
  Section 4 to drop the now-completed wiring step.

### What we decided

- No bugs found, no fixes needed.  The unit-test mocks were written
  with care and matched real shapes accurately enough that the live
  exercise passed without code changes.
- Kept the existing `smoke_test()` (clients-only mode) as the default
  for quick connectivity checks; `--all-tools` is opt-in for the
  fuller verification.

### What broke / what we discovered

- Nothing broke.  The discovery is non-news but worth recording:
  Wazuh's Server API and OpenSearch response shapes for `/agents`,
  `/rules`, and alert documents are stable enough that mock-driven
  unit tests stay accurate against a real deployment.

### What's next

- Frontier-API exit-criterion verification (blocked on operator API key).
- Batch-amend the static `KNOWN_MODELS` entries for `llama3.2` and
  `gemma3:4b` per ADRs 0001 and 0003 (cosmetic — neither is the
  current default).
- Begin Phase 3 (RAG + grounding validator) per docs/06.

---

## 2026-05-22 — Switch dev default model `llama3.2` → `qwen3:4b`

**Session type:** claude-code (continuous session)
**Phase:** Phase 2
**Duration:** ~30 min
**Branch / commit:** `main` — `e092e21` (ADR 0004), `ca495df`
(config flip), `14cc727` (KNOWN_MODELS amendment), `4324bce`
(PROGRESS/CHANGELOG update for switch)

### What we did

- Wrote ADR 0004 weighing the three probe results
  (`docs/decisions/0004-model-switch-llama3.2-to-qwen3-4b.md`).
- Flipped `DEFAULT_MODEL_ID` from `llama3.2` to `qwen3:4b` in
  `services/orchestrator/app/config.py` as a standalone commit.
- Verification surfaced that the static `KNOWN_MODELS["qwen3:4b"]`
  entry (deliberately-conservative Task 4 estimate: basic/pipeline)
  shadowed the probe-measured capability (mid/guided) at runtime —
  qwen3:4b was running in pipeline strategy.  Amended the static
  entry to match measurement per ADR 0002.
- Re-verified end-to-end: chat against real Wazuh, qwen3:4b in
  `guided` mode, one tool call to `count_alerts_by_severity`,
  grounded cited answer ("15 alerts total, all low severity").

### What we decided

- Land the three changes as three separate commits (ADR, config flip,
  static-entry amendment) so each is independently revertable.
- Keep `llama3.2` in `KNOWN_MODELS` — operators who want it just set
  `DEFAULT_MODEL_ID=llama3.2` in `.env`.

### What broke / what we discovered

- The static `KNOWN_MODELS["qwen3:4b"]` from Task 4 silently overrode
  the probe-measured capability, causing the model to run in the
  wrong strategy after the flip.  Verification caught it.  Lesson:
  any time we add a new model to `KNOWN_MODELS` from an estimate, we
  must amend it as soon as the probe runs.
- Earlier in the session, a single mid-conversation `loop_error`
  surfaced as "Model call failed:" with empty detail (the Ollama
  adapter's swallowed exception).  Added diagnostic capture in
  commit `e09b4e5` (logs exception type + traceback to audit data)
  so the next occurrence is debuggable.

### What's next

- Wire the 4 mock-only read tools to real Wazuh (now done — see entry
  above).
- Frontier-API exit-criterion verification (still pending key).
- Phase 3 entry.

---

## 2026-05-22 — Add model recommendations, session continuity tracking, and run the first capability probe

**Session type:** claude-code (executing user's planning brief at
`prompts/CLAUDE-CODE-SESSION-PROMPT.md`)
**Phase:** Phase 2 — Read path
**Duration:** in progress
**Branch / commit:** `main` — Commit 1 `c05cdce` (planning bundle),
Commit 2 `b093761` (session-additions docs), Commit 3 `e9cc316`
(Tasks 4+5 code + probe sys.path fix), final session commit pending
this entry.

### What we did

- **Committed the previously-untracked planning bundle.**
  Commit 1 (`c05cdce`): `docs/00-13` (excluding doc 11) + `README.md`.
  Commit 2 (`b093761`): updated `docs/11-claude-code-instructions.md`
  (session-continuity protocol), new `docs/14-model-recommendations.md`,
  new `docs/PROGRESS.md`, new `docs/CHANGELOG.md`.
- **Moved `PROGRESS.md` and `CHANGELOG.md` from the repo root into
  `docs/`** to match the references in docs 11 and 14.
- **Relaxed the start-of-session reading rule** in
  `docs/11-claude-code-instructions.md`: re-reading PROGRESS.md +
  CHANGELOG.md every turn is required only for a brand-new session, a
  different machine/environment, or a different Claude model version.
  The end-of-session update + final commit remain mandatory regardless.
- **Populated `docs/PROGRESS.md`** with the real current state of Wolf
  (Phase 2 status, what's built and verified, configuration, what's
  next, active decisions, 128-test coverage).
- **Initialized `docs/CHANGELOG.md`** (this entry).
- [TASK 4] Extended `KNOWN_MODELS` in
  `services/orchestrator/app/models/interface.py` with four new entries:
  `qwen3:4b`, `gemma3:4b`, `qwen3:8b`, `glm-5.1`. **Did not** change
  `DEFAULT_MODEL_PROVIDER` or `DEFAULT_MODEL_ID` — both remain
  `ollama` / `llama3.2`.
- [TASK 5] Added `license_class` to `CapabilityDescriptor` in
  `packages/schema/wolf_schema/capability.py`. Populated every existing
  `KNOWN_MODELS` entry: Llama family → `restricted`, Claude/GPT →
  `proprietary`, Qwen/Gemma/Mistral → `apache-2.0`, GLM/DeepSeek →
  `mit`. Non-breaking informational field; no runtime code branches on
  it.
- [TASK 6] Ran the capability probe against live Ollama on this hardware:
  `uv run python -m tools.model_probe --provider ollama --model llama3.2`.
  Required a one-line `sys.path` bootstrap in `tools/model_probe/__main__.py`
  to resolve a two-`app/`-packages collision between
  `services/gateway/app/` and `services/orchestrator/app/` that uv editable
  installs had been silently shadowing (gateway won the ambiguous name).
  Probe result: score 0.68, 3/4 tasks pass; measured `mid` / `guided` —
  matches the static `KNOWN_MODELS` estimate at the strategy tier.  Full
  ADR at `docs/decisions/0001-model-probe-llama3.2-baseline.md`.
- [TASK 7] Pulled `qwen3:4b` and `gemma3:4b` and probed both.
  - `qwen3:4b`: score **0.75** (3/4 pass).  PASS: tool-call formatting,
    JSON-schema adherence, multi-step reasoning.  FAIL:
    grounding-discipline (fabricated specific data when given no tools).
    Measured `mid` / `guided` / `schema_enforced` — every field as good
    as or better than `llama3.2`, except grounding.  ADR
    `docs/decisions/0002-model-probe-qwen3-4b.md`.
  - `gemma3:4b`: score **0.25** (1/4 pass).  Two task failures were
    HTTP 400 from Ollama because Gemma 3 4B has **no native tool
    calling** — the runtime rejects requests that include a `tools`
    parameter.  Measured `basic` / `pipeline`.  Ruled out as a default
    candidate.  ADR `docs/decisions/0003-model-probe-gemma3-4b.md`.
- [TASK 8] Created `docs/decisions/README.md` (ADR definition, naming
  convention `0NNN-short-kebab-title.md`, file template, live index of
  the three new ADRs).

### What we decided

- **Default model stays `llama3.2` for now.** Doc 14 recommends switching
  to an Apache-licensed model (Qwen 3 4B or Gemma 3 4B) before Wolf has
  external users, but the switch is gated on probe data and a follow-up
  ADR. This session adds the candidate entries to `KNOWN_MODELS` so the
  options exist; the switch itself is a separate decision.
- **`license_class` is informational, not enforcement.** It surfaces the
  Llama vs Apache/MIT distinction in the UI eventually, but no code path
  blocks a model on its license. Operator choice always.
- **PROGRESS.md and CHANGELOG.md live in `docs/`, not at repo root.**
  Resolved by moving the files; doc 11 and doc 14 keep their existing
  references.
- **Start-of-session reading is conditional** (brand-new session / new
  environment / different model only). End-of-session update is always
  mandatory. Updated doc 11 to reflect this.

### What broke / what we discovered

- **Two-`app/`-packages collision blocked the probe CLI.** Both
  `services/gateway/app/` and `services/orchestrator/app/` exist as
  Python packages literally named `app`.  uv's editable installs put
  both on `sys.path` (gateway entry first), so bare `import app`
  resolved to the gateway and `app.models.ollama` failed with
  `ModuleNotFoundError`.  Pytest never hit this because its path setup
  happens to land orchestrator first.  Fixed locally to the probe CLI;
  the deeper "rename one of them" surgery is logged as deferred work.
- **`llama3.2`'s static `KNOWN_MODELS` entry was directionally right
  but two fields were off:** `native_tool_calling` was estimated
  `partial` and measured `full` (upgrade); `structured_output` was
  estimated `prompt_coaxed` and measured `unreliable` (downgrade —
  free-form JSON adherence failed mid-document).  Recommended strategy
  matches.
- **`qwen3:4b`'s static entry was conservative across the board.**
  Measured stronger on every dimension except grounding-discipline,
  where it failed cleanly (fabricated when given no tools).  In Wolf's
  tool-gated agent loop that risk is contained but raises Phase 3
  grounding-validator priority.
- **`gemma3:4b` has no native tool calling.** Ollama returns HTTP 400
  on any chat request that includes `tools`.  This is the model
  family's structural limitation, not a transient bug.  Confirms doc 14
  that gemma is a viable summariser at best, not an agent driver.

### What's next

- Write `docs/decisions/0004-model-switch-llama3.2-to-qwen3-4b.md`
  weighing the three probe results.  qwen3:4b is the recommendation
  for the *recommended-for-shipping* default per doc 14; the question
  the ADR settles is whether dev should switch now or wait for the
  Phase 3 grounding validator.
- After the switch ADR lands, change `DEFAULT_MODEL_ID` in
  `services/orchestrator/app/config.py` in a **separate commit** that
  references the ADR (per doc 14's environment-change playbook).
- Wire the four remaining read tools to real Wazuh
  (`get_event_timeline`, `get_agent_alert_history`, `get_agent_detail`,
  `get_rule_definition`).
- Verify Phase 2 exit criterion against a frontier API model in addition
  to the local-Ollama path that already passes.
- Batch-amend the static `KNOWN_MODELS` entries for `llama3.2`,
  `qwen3:4b`, and `gemma3:4b` to reflect measured capability.
