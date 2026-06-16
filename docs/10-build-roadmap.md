# 10 — Build Roadmap

This is the phased plan that builds the platform in the right order
— earliest phases prove the riskiest assumptions, latest phases add
capabilities that are optional or that benefit from the experience
of running the earlier ones.

**This roadmap is the recommended order for the coding agent to
implement.** It reflects the actual build state as of 2026-06-11;
see `docs/CHANGELOG.md` for the full slice-by-slice history and
`docs/PROGRESS.md` for the live current-phase pointer.

---

## Where Wolf stands today (2026-06-11)

**Phases 0–4 + 5.0–5.10: CLOSED.** Wolf has a working agent loop
against a real Wazuh, three-component architecture (wolf-server /
wolf-dashboard / wolf-database) per ADR 0016, mTLS substrate,
RAG + grounding validator, multi-organization with cross-organization
isolation suite, systemd-deployable daemons, and APT packaging
that builds + installs cleanly on Ubuntu/Debian.

**Major design arc closed 2026-06-10 — 2026-06-11.** Four ADRs
ACCEPTED after multi-round operator reviews:
- **ADR 0017** — Wolf Central Brain (memory + thinking +
  self-validation + continuous learning) — drives Phases 7.5 + 8.5
- **ADR 0018** — Bootstrap Superuser + Per-Org RBAC + Login UX —
  drives Phases 6.4 + 6.5
- **ADR 0019** — Web-first configurability mandate (GUI ↔ CLI sync
  discipline applied to every future configurable knob)
- **ADR 0020** — Superuser-owned Wazuh component mapping — drives
  Phase 6.6

**Phase 6.4 (tenant→organization codebase rename): ✅ SHIPPED
2026-06-11** (main @ `3f000cb`, all 14 CI jobs green). Actual scope:
~170 files, single PR, 1 session. **Phase 6.5-a is the next active
slice.**

**Phase 6+ (Approval Gateway and beyond): designed but not yet
started.** Sub-phase ordering 6.4 → 6.5 → 6 → 6.6 → 6.9 → 6.7 → 6.8 → 7
→ 7.5 → 8 → 8.5 → 9 → 9.5 → 10 → 11 → 11.5 → 12 → 13. Reflects the ADR-
derived sequencing; see `## Phase ordering — divergence from the
original plan` below. (6.9 — outbound email — executes before 6.7
despite the higher number, so notifications ship with an email channel;
ADR 0022.)

---

## Phase 0 — Foundations ✅ CLOSED

- Repo, license, CI skeleton (Apache 2.0, lint, type-check, test).
- `docker-compose.yml` bringing up Postgres, pgvector, a minimal
  wolf-server (then "orchestrator") process.
- Auth scaffolding: local-account login flow + OIDC adapter (SSO
  configuration deferred to the operator).
- Organization data model (`organizations`, `users`, `user_organizations`, `roles`)
  + the immutable request-context construct.
- Secrets-backend interface with an encrypted-file backend.
- Structured logging, OpenTelemetry tracing, audit-log skeleton.

**Exit criteria (met):** developer can `make up`, log in, and the
system records an audit event for the login.

## Phase 1 — The model abstraction ✅ CLOSED

The single most important risk to retire early, because the
project's promise depends on it.

- `ModelProvider` interface + capability descriptor.
- Three adapters: **Anthropic**, **OpenAI** (also covers OpenAI-
  compatible like OpenRouter), **Ollama**. Gemini / DeepSeek / etc.
  follow when needed.
- Structured-JSON-output fallback for adapters without reliable
  native tool-calling.
- `tools/model_probe` — self-test that grades a configured model
  and outputs its capability descriptor (ADRs 0001/0002/0003).
- Per-model documented capability tier.

**Exit criteria (met):** developer configures any of the three
providers, runs the probe, and wolf-server picks the matching
strategy (frontier/guided/pipeline).

## Phase 2 — The read path, end to end ✅ CLOSED

Delivers the first real user value and proves the agent loop works.

- Wazuh OpenSearch client with **forced organization filter** in the
  query layer.
- Wazuh Server API client (read endpoints only).
- Tool registry with strict input/output schemas (Pydantic).
- Nine read tools: `search_alerts`, `aggregate_alerts`,
  `get_event_timeline`, `get_agent_alert_history`, `list_agents`,
  `get_agent_detail`, `get_rule_definition`, `get_cluster_health`,
  + the `query_runbook` tool added in Phase 3.
- Wolf-server's agent loop with three strategies (frontier /
  guided / pipeline).
- Resource guardrails enforced before tool execution.
- Audit logging for every model call and tool call.
- Minimal Next.js wolf-dashboard.

**Exit criteria (met per ADR 0005):** analyst can ask "why did
agent X trigger alert Y at time Z?" and receive a grounded, cited
answer. Verified on a frontier model (Nemotron 120B via OpenRouter)
**and** a local Ollama model (`qwen3:4b`).

## Phase 3 — The RAG / knowledge layer ✅ CLOSED

- Vector store interface; pgvector implementation.
- Ingestion pipeline: structure-aware chunking, metadata extraction.
- Seed corpora: Wazuh docs (`tools/seed_knowledge`), ATT&CK.
- Hybrid retrieval (vector + BM25).
- `query_runbook` tool with metadata filters as first-class
  arguments.
- Grounding validator (`wolf_server/grounding/`) — rejects
  ungrounded factual claims in answers.
- Per-organization private corpus partition (foundation for organization
  runbooks, even though uploads come later).

**Exit criteria (met):** asking about Wazuh behavior produces an
answer that cites doc chunks; asking about ATT&CK techniques cites
versioned ATT&CK content.

## Phase 4 — Multi-organization hardening ✅ CLOSED

Crucial before any MSSP-targeted feature.

- Organization onboarding with connection validation and immutable
  profiles.
- Per-organization credential storage in the secrets backend.
- Connection pooling per organization (stateless checkout-and-establish).
- Cache wrapper with mandatory organization-prefix keys.
- **Cross-organization test suite** (`tools/organization_isolation_test`)
  running in CI.
- Audit-stream organization scoping verified by the test suite.

**Exit criteria (met):** isolation suite passes for every read
tool, RAG retrieval, audit query, and cache path. Two organizations
operate side-by-side with verifiable separation.

---

## Phase 5 — Deployment substrate (the build's biggest reshape)

Phase 5 was originally scoped as "Cases and reporting." That work
got bumped to Phase 7 (see `## Phase ordering` below). What
actually happened in Phase 5 was the deployment-infrastructure
sub-tree that turns Wolf from "deploys on a dev shell" to "deploys
as proper daemonised services with one apt install." Eleven
sub-phases over ~3 months:

### Phase 5.0a–c — Dashboard UX polish ✅ CLOSED

- Conversation tree with branching (Edit / Retry navigator).
- Verdict chips (Verified / Uncertain / Not Verified / Non-factual).
- Progressive token-by-token rendering.
- Live activity feed; Stop button; concurrent per-conversation
  streams.
- Chats-history overlay with full-text search across every branch.
- Platinum / Dusk Blue / Steel Blue / Icy Blue colour palette.
- All 12 sub-slices (5.0c-a through 5.0c-l) shipped between
  2026-05 and 2026-06-02. See CHANGELOG for the full chain.

### Phase 5.4 — Native HTTPS + `wolf-cert` CLI ✅ CLOSED

- Self-signed CA + 100-year leaf certs.
- `wolf-cert` CLI (init/status/export-ca/add-host/renew/revoke).
- Three built-in leaves: `server`, `dashboard`, and (added in
  5.6-b) `dashboard-client`.
- Operator runs `wolf-cert init` → both wolf-server and
  wolf-dashboard auto-detect the cert files and flip to HTTPS.

### Phase 5.5 — Component renaming refactor ✅ CLOSED

Pure refactor per ADR 0016:
- `services/orchestrator/` → `services/server/`
- `services/orchestrator/app/` → `services/server/wolf_server/`
- `frontend/` → `services/dashboard/` (package name
  `wolf-dashboard`)
- `services/gateway/app/` → `services/gateway/wolf_gateway/`
- `wolf-cert init` mints leaves named `server`/`dashboard` (was
  `orchestrator`/`frontend`).
- Permanently kills Gotcha #1 (two-`app/`-packages collision)
  because the new package names cannot collide.

### Phase 5.6 — Edge-component architecture + mTLS ✅ CLOSED

The slice that kills the cross-origin NetworkError that surfaced
under HTTPS. Five sub-slices:

- **5.6-a** — Next.js catch-all reverse-proxy at
  `services/dashboard/app/api/[...path]/route.ts`. Browser only
  ever talks to wolf-dashboard's origin.
- **5.6-b** — `wolf-cert` mints a third leaf,
  `dashboard-client` (`LeafKind.CLIENT`, CN
  `wolf-dashboard-client`).
- **5.6-c** — wolf-server requires mTLS via uvicorn's
  `ssl_cert_reqs=CERT_OPTIONAL` + `MtlsMiddleware` that
  enforces the CN allowlist + bypasses GET /healthz from
  loopback. Dashboard proxy presents the dashboard-client
  cert via undici Agent.
- **5.6-d** — Launcher banner polish (`mTLS: ENABLED/DISABLED`),
  ONBOARDING §3.12 rewrite + new §3.13 for distributed
  deployment.
- **5.6-e** — `make smoke-mtls` + CI job.

### Phase 5.7 — `wolf-database` extraction ✅ CLOSED

Postgres becomes the third deployable Wolf component (per ADR
0016), parallel to wolf-server / wolf-dashboard / wolf-gateway.
Four sub-slices:

- **5.7-a** — wolf-database substrate (new `packages/database/`
  workspace package): `DatabaseLayout`, `find_postgres_binaries()`,
  `PostgresqlConfOptions` + `PgHbaOptions` templates,
  `connection_url()`.
- **5.7-b** — `wolf-database` CLI (init/start/stop/status/
  reconfigure). Wraps system-installed `postgresql-17` +
  `postgresql-17-pgvector` binaries with Wolf-owned config +
  lifecycle.
- **5.7-c** — Makefile wrappers + `.env.example` rewrite +
  ONBOARDING §3.4 three-path comparison.
- **5.7-d** — `make smoke-database` + CI job + Phase close-out.

### Phase 5.8 — systemd units + FHS install paths ✅ CLOSED

Turns the three components into proper daemonised services. Four
sub-slices:

- **5.8-a** — User-level systemd units (`deploy/systemd/dev/`)
  + `make install-user-systemd`. Per ADR 0016 v3, fully
  independent (no `After=`/`Requires=`/`Wants=` between Wolf
  units). wolf-server got a `_wait_for_database()` retry loop
  so a fresh boot tolerates wolf-database not being ready.
- **5.8-b** — System-level units (`deploy/systemd/system/`) with
  per-component service users (`wolf-database`, `wolf-server`,
  `wolf-dashboard`, `wolf-gateway`) in a shared `wolf` group.
  Hardening directives applied. `install-users.sh` creates
  users + group + FHS data + config dirs.
- **5.8-c** — `/usr/bin/wolf-*` shipped CLI shims + `install.sh`.
- **5.8-d** — ONBOARDING Path A rewrite (production-recommended);
  `make smoke-systemd` + CI job; Phase close-out.

### Phase 5.9 — APT packaging — pending

`.deb` package that wraps the deployment chain:
- `Depends: postgresql-17, postgresql-17-pgvector, nodejs`.
- Post-install hook invokes `install-users.sh` + `install.sh`.
- Creates the Python venvs at `/usr/lib/wolf-{database,server}/.venv/`
  via `python -m venv` + `pip install` the workspace packages.
- Runs `npm run build` for wolf-dashboard with
  `output: "standalone"` so the Next.js server is ready at
  `/usr/lib/wolf-dashboard/.next/standalone/server.js`.
- Enables + starts the three systemd units.

**Exit criteria:** `sudo apt install wolf` → reboot → all three
services running, browser at `https://<host>/` answers HTTP 200.

### Phase 5.10 — DNF packaging — pending

RPM equivalent of 5.9. Same install-time work, different
packaging tooling (rpmbuild / dnf).

**Exit criteria:** `sudo dnf install wolf` produces the same
end-state as the APT path.

---

## Phase 6 — Propose tools and the Approval Gateway

The most safety-critical work in the project. Built after the
read-side platform is solid + the deployment substrate is in
place. The wolf-gateway service (currently a Phase 0 stub at
`services/gateway/wolf_gateway/`) becomes a real service here.

Builds on Phase 5.6's mTLS substrate — wolf-gateway will get its
own client cert (`wolf-gateway-client`, parallel to
`wolf-dashboard-client`) added to wolf-server's
`MTLS_ALLOWED_CLIENT_CNS` allowlist.

- The **separate gateway service** with its own credentials.
- Proposal data model and state machine.
- Propose tools: `propose_active_response`, `propose_rule_tuning`,
  `propose_agent_action`, `propose_config_change`.
- Approval authority model: organization, action class + severity,
  target sensitivity.
- Crown-jewel tagging.
- Approval queue UI: shows evidence, resolved target, rationale.
- Signed approval tokens bound to content hash.
- Execute tools — **only** inside wolf-gateway, never in
  wolf-server.
- Freshness re-check on `approved → executing`.
- Verification read after execution.
- Rollback path for reversible actions.
- Audit transitions for every proposal state change.
- Separation of duties: requester cannot approve.
- Four-eyes for critical-severity actions.

**Ship v1 with no auto-execution** (see `04`).

**Exit criteria:** analyst requests an active response, a
different analyst with the right authority approves, wolf-gateway
executes it against a real Wazuh deployment, verification read
confirms the actual state. Every step audited.

## Phase 6.4 — tenant → organization codebase rename — ✅ SHIPPED 2026-06-11

**Per ADR 0018 (ACCEPTED 2026-06-10). SHIPPED 2026-06-11** — main @
`3f000cb`, 4 commits (`076febd` rename, `a7d0aed` httpx2 test-dep,
`e382674` CI workflow paths, `3f000cb` migration FK fix), all 14 CI
jobs green. Pre-requisite for Phase 6.5 and all subsequent phases.
The entire codebase migrated from the "tenant" terminology to
"organization":

- DB: `tenants` table → `organizations`; `tenant_id` columns →
  `organization_id`; FK constraints renamed by dynamic pg_constraint
  lookup (legacy Postgres auto-names vs NAMING_CONVENTION names —
  both shapes converge post-0007)
- Alembic migration 0007 renames atomically (Postgres-native
  `ALTER ... RENAME`, in-place, reversible `downgrade()`)
- SQLAlchemy models: `Tenant` → `Organization`; `UserTenant` →
  `UserOrganization`; `TenantContext` → `OrganizationContext`
- Frontend: `tenant-switcher.tsx` → `organization-switcher.tsx`;
  all TypeScript types + variable names + React contexts
- Test fixtures + factory helpers; `tools/tenant_isolation_test/`
  → `tools/cross_organization_isolation/`
- Memory entry `tenant-renamed-to-organization.md` flipped from
  STANDING RULE to COMPLETED

Actual scope: 1 session, ~170 files (144 Python swept + frontend +
docs + config), ~1500 substitutions, 8 git-mv renames.

**Exit criteria — all met:** every functional reference to
`tenant`/`tenant_id`/`Tenant` renamed (remaining occurrences are
immutable history only: ADRs, migrations 0001-0006, the rename
memory file). 397 tests pass, 0 skipped, 0 warnings.
Cross-organization isolation suite (formerly cross-tenant isolation
suite) green locally + in CI.

## Phase 6.5 — Bootstrap Superuser + Per-Org RBAC + Login UX

**Per ADR 0018 (ACCEPTED 2026-06-10).** The first multi-organization-
ready Wolf release. Builds on Phase 6.4's rename + Phase 6's
wolf-gateway role-gate hook. 9 sub-slices, **12-13 sessions
estimated**:

1. **6.5-a — Bootstrap Superuser + org-recovery** — ✅ **SHIPPED
   2026-06-11.** `deploy/bin/bootstrap_superuser` wrapper +
   `wolf_server/bootstrap/superuser.py` core (create-if-absent,
   32-char password printed once, --rotate-password, wrapper-only
   guard); `.deb` postinst best-effort auto-create + operator
   instruction step; Superuser password reset for any user
   (audit-emitted; own account refused — CLI is its recovery path);
   break-glass org-recovery endpoint refused while any active Admin
   exists; login accepts username "Wolf" → org-less superuser
   session (organization_id None). 18 tests.

2. **6.5-b — Role enforcement (Phase 6.5 subset only)** — ✅
   **SHIPPED 2026-06-11.** Capability matrix
   (`organization/rbac.py`, mirrors ADR 0018 row-for-row) +
   `require_capability()` dependency + "Last Admin" invariant
   guard; role rename approver→responder + new engineer role
   (data migration 0008); org CRUD API (Superuser-only); org
   user-management API (Admin, Last-Admin-guarded); Superuser-
   membership consent-gate endpoints (grant/revoke, dual
   org+install audit); org audit-log view (Admin + Responder);
   chat gated via `require_capability(CHAT)`; `wolf_server/api`
   joined the strict-mypy set. 25 tests. **Propose / approve /
   execute decorators DEFERRED to Phase 6 (wolf-gateway)** — role
   values exist + the ADR documents intent, but the plumbing that
   USES these capabilities ships with Phase 6.

3. **6.5-g — Session cookie blacklist infrastructure** — ✅
   **SHIPPED 2026-06-11.** `SessionBlacklist` protocol with TWO
   backends (operator choice, Slice 4.3 precedent): in-memory
   default (correct for the single-process native install) +
   Redis activated by `REDIS_URL` (multi-worker /
   restart-surviving; redis *client* is a regular dep, the
   *server* is operator-managed — never a .deb dependency).
   AuthMiddleware checks the blacklist on every authenticated
   request (revoked → 401 + cookie cleared). Triggers: logout
   (session-scoped, TTL = remaining token life), Superuser
   password-reset + new force-revoke endpoint
   `POST /api/v1/users/{id}/sessions/revoke` (user-watermark:
   ALL outstanding sessions die, later re-logins live).
   `wolf_server/auth` joined the strict-mypy set. 13 tests.

4. **6.5-c-i — Backend header-based org context** — ✅ **SHIPPED
   2026-06-12.** `X-Organization-Id` header names the org per
   request (cookie = user, header = org, 6.5-b gate = permission);
   membership validated on every request — the header selects
   among memberships, never grants. Centralized in
   `require_organization_context` (6.5-b's uniform gating meant
   ONE dependency change covered every org-scoped endpoint).
   Transitional JWT-claim fallback when the header was absent
   (removed 2026-06-12 at c-ii sign-off, together with login's
   organization_id field and the token's org/role claims — the
   access token is now `sub`+`session_id` only).
   Login gained the ADR's three-shape response
   (Superuser+redirect / auto-selected / needs_org_selection
   with memberships; zero-memberships → 401 contact-your-admin;
   inactive orgs excluded). New audit-recording
   `POST /auth/select-organization` + `/auth/switch-organization`;
   `/me` reflects the header org (per-tab profile chip). 14 tests
   incl. the two-tabs-two-roles workflow.

5. **6.5-c-ii — Frontend login + per-tab org state** — ✅ **SHIPPED
   2026-06-12**, operator manual web-test signed off same day
   (all four checks). Login form is
   email+password only; three-shape handling (Superuser →
   `/superuser/dashboard` placeholder page, real UI is 6.5-d;
   auto-selected → `/chat`; needs-selection → inline org picker in
   the login card); per-tab `sessionStorage`
   (`lib/org-context.ts`) + `X-Organization-Id` on every API call
   (incl. the SSE chat stream); org-switcher switches per-tab with
   NO re-login (audit via switch-organization); auth-provider
   self-heals stale tab org (403 → clear + retry) and auto-selects
   for single-org users in fresh tabs. Live-validated end-to-end
   through the HTTPS proxy. **Backend transitional fallback removed
   2026-06-12 after the operator's sign-off** — token org/role
   claims stripped, header-absent → 401, login org field gone,
   tests refactored 1:1 (467 green).

6. **6.5-d — Organizations + Superuser-dashboard UI** — ✅ **SHIPPED
   2026-06-14**, operator manual web-test signed off same day (all
   four checks). Guarded `/superuser` shell (Dashboard · Organizations
   · Audit) with the role guard lifted into the layout; Organizations
   page (list / create with slug / rename / soft-delete, deleted shown
   with a badge); per-org page seeds the first Admin via the
   break-glass recovery endpoint (one-time password shown + copy;
   409-aware once an Admin exists — no member listing, consent gate
   held); install-wide audit view (new Superuser-only
   `GET /api/v1/superuser/audit`, paginated, org name joined). Org-less
   rows are labelled **"System"** (matching the AuditEvent model's own
   wording), distinct from the **install-wide** VIEW scope. 4 new
   backend tests (471 total green), mypy --strict clean, frontend gate
   green, live-smoked through the HTTPS proxy.

7. **6.5-e — User management UI (per-org)** — ✅ **SHIPPED
   2026-06-14**, operator manual web-test signed off same day (all
   seven checks). Admin-only `/settings` area (guard layout) reached
   from the chat-header Settings gear; Users page lists members
   (name/email/role/member-since/status) with an inline role dropdown
   (`PATCH /role`), add-member dialog (one-time password for brand-new
   accounts), remove with confirm, and a "Recent member changes" panel
   filtering the org audit to `organization.member.*`. Backend was
   already complete (6.5-b); the Last-Admin 409 guard surfaces in the
   UI. Frontend-only — no `services/` change. Frontend gate green,
   live-smoked through the proxy.
   - **6.5-e.1 — Org-Admin password reset — ✅ SHIPPED 2026-06-14**
     (operator signed off). New `POST /api/v1/organization/users/{id}/
     password-reset` (Admin-gated, member-scoped, one-time password,
     revokes the member's sessions, dual-audited); "Reset password"
     action on the Users page (confirm → one-time reveal + copy). 4
     tests; 475 backend green. No SMTP/self-service reset exists, so
     recovery is Admin-driven.
   - **6.5-e.2 — Superuser break-glass reset-by-email — ✅ SHIPPED
     2026-06-15** (operator signed off). New `POST /api/v1/users/
     password-reset-by-email` (Superuser-only, 404 unknown / 409
     Superuser-self, revokes sessions, audited `via:email`); break-glass
     "Reset a member's password" card on the Superuser per-org page.
     Recovers the locked-out *sole Admin* the org-scoped reset can't
     reach, by email (no roster listing — consent gate held). Shipped
     alongside an input-validation + exception-handling fix set (see
     6.5-i) — the `[object Object]` 422-render bug, `isValidEmail`
     inline checks, and `RecoveryAdminRequest.display_name` bounds.

8. **6.5-f — Superuser-membership-grant flow + UI — ✅ SHIPPED
   2026-06-15.** ADR 0018 consent gate, end to end. The install
   Superuser holds **zero** org data access until an org Admin
   grants it: Superuser **requests** (reason + proposed duration,
   default 24h or until-revoked) → Admin **approves** (honour /
   override-hours / until-revoked) **or rejects** → time-limited
   `UserOrganization` row (role `superuser`, `expires_at`) →
   **revoke** (Admin) or **lazy expiry** (no scheduler — pruned at
   access time in `require_organization_context` + the banner
   endpoint). Migration 0009 (`user_organizations.expires_at` +
   `superuser_access_requests`). The direct-grant precursor was
   **replaced** (single clean path; tests rewritten).
   - **Activity timeline:** each request is a full lifecycle record
     — `ended_at` + terminal statuses `revoked`/`expired`, stamped
     on revoke and in `expire_if_past`. Settings → Access renders
     the per-request timeline (Requested → Approved/Rejected/
     Cancelled → Revoked/Expired, with actor + timestamps); the
     Superuser org-detail card mirrors the terminal states.
   - **All-member transparency banner** (state-derived, no
     notifications table): poll + route-change + window-focus; the
     backend runs lazy expiry so it self-clears on lapse/revoke.
     **Fully dismissable** (operator choice 2026-06-15) — per-grant
     sessionStorage key; a new grant or next login re-surfaces it.
   - **Superuser chat-nav gate** (operator-found, folded in): an
     org-less Superuser is bounced off `/chat` to the install-admin
     dashboard; the **Chat** nav there is disabled until a grant
     lands, then unlocks (regular org users unaffected).
   - **MSSP message hygiene** (operator-found, folded in): the
     "can't touch the Superuser" rejections (password / role /
     remove + the misconfigured-install guard) no longer leak
     internal endpoints, HTTP verbs, or the bootstrap CLI to a
     tenant Admin — generic "— Unauthorised."/"revoke their access
     instead" wording; the install-topology diagnostic goes to the
     **server log**. Regression test asserts no `/api/`·CLI leak.
   - Gate: **491 backend + cross-org isolation + mypy --strict**
     green; `alembic check` clean (model/migration parity);
     frontend tsc/eslint(0)/build green; operator web-tested (all
     5 checkpoints + the three refinements). No CI workflow change
     needed (existing typecheck/test/frontend/alembic-check jobs
     already cover the touched surfaces). Commits `<this>`.
   - **Deferred to dedicated phases (operator-requested
     2026-06-15):** a real per-user **notification system** (Phase
     6.7) and **SSE real-time push** (Phase 6.8) — see ADR 0021.
     The banner's in-app+poll model is the v1 placeholder.

9. **6.5-h — Invite-link verification flow — ✅ SHIPPED 2026-06-16.**
   ADR 0018 item 9 was **split** (operator decision 2026-06-15): the
   invite-verification flow ships here; the same-network gate becomes
   **6.5-h.2** (see below) because a robust gate needs dashboard-tier
   work the verify flow doesn't.
   - **DB (migration 0010):** `User.verification_status`
     (`unverified`/`verified`) + `verification_token_hash` (SHA-256 of a
     256-bit single-use token; only the hash is stored) +
     `verification_token_expires_at` (7 days). Migration backfills every
     pre-existing row to `verified` so no current user is locked out.
   - **Gate:** enforced in `require_organization_context`
     (organization/context.py) — the chokepoint for ALL org data; it
     already eager-loads `binding.user`, so the check is zero extra query.
     `/me`, `/me/organizations`, `verify-invite`, `logout` stay reachable
     so an unverified user can escape the gate. Bootstrap / recovery /
     Superuser accounts are created `verified` (4 `User(...)` sites set it
     explicitly); the gate never affects them.
   - **Endpoints:** `create_member` mints `unverified` + a token (raw
     returned ONCE beside the one-time password); new
     `POST /organization/users/{id}/regenerate-invite-link` (Admin, recover
     a lost link — old token invalidated; 409 if already verified); new
     authenticated `POST /auth/verify-invite {token}` (409 already-verified;
     403 missing/expired/mismatch — token NOT consumed on failure; on
     success flip to `verified`, clear the token, audit). `MeResponse` +
     `LoginResponse` carry `verification_status`.
   - **UI:** `/verify` paste-the-link screen (extracts the token from a
     pasted link or bare token); routing guards send an unverified
     non-superuser to `/verify` (login routes there directly — no
     `/chat` hop — and the chat layout redirects as defense-in-depth);
     Users page shows a Verified/Unverified badge (+ invite expiry) and a
     "Generate invite link" action; Add-member + invite reveals copy the
     link once. Dialog primitive hardened (frosted backdrop matching the
     chats overlay, content-sized + scroll-safe, wider modals, full link
     wrapped — operator UI/UX pass).
   - **Audit (isolated from notifications):** `…invite_generated` (in
     member.added data), `organization.member.invite_regenerated`,
     `auth.invite_verification.succeeded` / `…failed{reason}`.
   - **Gate:** 431 backend + cross-org isolation + mypy --strict green;
     `alembic check` clean (0010 round-trips on Postgres); frontend
     tsc/eslint(0)/build green; operator web-tested + a headless-Chrome
     check confirmed login→`/verify` and `/chat`→`/verify` for unverified
     users. No CI workflow change needed (touched modules already in the
     strict-mypy set; alembic-check covers 0010). Commits `<this>`.

9b. **6.5-h.2 — Same-network verification gate — ✅ SHIPPED 2026-06-16.**
    ADR 0018's "verify only from inside Wolf's network" check (see
    **ADR 0023** for the topology decision). **Why it was its own slice:**
    the browser only ever talks to the dashboard (single-origin, ADR 0016);
    the proxy forwards to wolf-server, so wolf-server sees the *dashboard's*
    IP, not the browser's. Next 16 exposes no socket to route handlers, and
    its `x-forwarded-for ??= socket.remoteAddress` PRESERVES a client-supplied
    XFF — so reading XFF inside Next is spoofable.
    - **Edge proxy (not a custom Next server):** a small Node TLS proxy
      (`services/dashboard/scripts/edge-proxy.mjs`, stdlib-only) terminates
      TLS, owns the browser socket, **strips** any client
      `x-wolf-client-ip`/`x-forwarded-for`/`x-real-ip` and **stamps** the
      real `socket.remoteAddress` as `X-Wolf-Client-IP`, then forwards to an
      UNMODIFIED `next dev` / standalone `server.js` on a loopback inner
      port. Next stays 100% stock (Turbopack dev + standalone prod
      untouched). `scripts/dev.mjs` rewired; prod shim + `debian/*.install`
      + unit comments updated; SSE streaming preserved (verified: chunks
      arrive incrementally through the proxy).
    - **Gate (wolf-server):** new `wolf_server/network/local_network.py`
      enumerates the host's NIC CIDRs (via `ifaddr`) + loopback;
      `verify-invite` trusts `X-Wolf-Client-IP` **only** when the request is
      mTLS-authenticated as the dashboard (`request.state.mtls_cert_cn`),
      else falls back to the real TCP peer — a direct caller can't forge it.
      Out-of-network → 403 `wrong_network` **without consuming the token**
      (retry from the right network). **OFF by default** — the gate is an
      on-prem single-network control and a default-ON would block remote
      **MSSP** client orgs (wolf-server lives in the provider's network);
      on-prem operators opt in with `SAME_NETWORK_GATE_ENABLED=1` (startup
      banner prints the state). Env-only for now → becomes a synced Superuser
      toggle in the **config-settings phase** (below); **per-org trusted
      networks** is the MSSP-correct evolution. `/verify` page gains a
      "verify from your org's network" hint.
    - **Gate (CI):** ruff + mypy --strict (+`wolf_server/network`) + 449
      backend / 72 package tests (0 skip) + cross-org isolation + dep-audit
      (`ifaddr` clean) green; frontend tsc/eslint/build green; edge proxy
      strip/stamp + no-buffering validated in isolation; live HTTPS + login
      (mTLS) + spoof-strip confirmed. smoke-deb-install asserts edge-proxy.mjs
      ships. Commits `<this>`.
    - Out of scope (later): operator-configurable `WOLF_TRUSTED_ADDITIONAL_CIDRS`
      (cloud/multi-network) + a Superuser GUI toggle for the gate
      (web-first-configurability debt — env-only this slice).

10. **6.5-i — Input-validation + exception-handling hardening pass —
    ✅ SHIPPED 2026-06-15** (operator-mandated 2026-06-15; see memory
    `input-validation-exception-handling`). A dedicated retrofit
    audit so EVERY input field across the project — login, chat
    composer, org CRUD, member management, role change, all password
    flows, conversation rename, any field shipped before this rule —
    meets the bar: server-authoritative validation (pydantic
    `Field`/`EmailStr`/allowlists/patterns), client-side mirror for
    inline UX, and human-readable, field-relevant error messages
    (never `[object Object]`; surface `ApiError.message` via the
    shared `formatApiDetail`). Each constraint gets a test. The
    standing rule applies inline to all NEW fields from 2026-06-15;
    this slice closes the gap for pre-rule fields. Partly seeded by
    6.5-e.2's fixes (`unwrap`/`formatApiDetail`, `isValidEmail`,
    `RecoveryAdminRequest.display_name`).
    - **Audit result:** the backend was already largely at the bar
      (`chat.py`/`organizations.py`/`org_management.py`/`superuser.py`
      all use `Field`/`EmailStr`/allowlists). Two real gaps closed:
      (1) `auth.py LoginRequest` email/password were unbounded → added
      `Field(max_length=320)` / `Field(max_length=1024)` (payload-size
      cap; `email` stays plain `str` so the Superuser username "Wolf"
      and `wolf@wolf.local` still log in; **no** `min_length` — login
      must not probe credential shape) + `test_login_rejects_oversized_fields`.
      (2) Frontend client-side mirrors: `chat-composer` 4000-char cap
      (matches backend `question`) + near-limit counter + send guard +
      native `maxLength`; `chat-sidebar` rename empty/whitespace guard
      (`commitRename` reverts blank); `login-form` `noValidate` +
      inline empty-field messages (app-native, not the browser bubble)
      and a borderless inline error.
    - **Intentionally NOT constrained** (recorded so the audit is
      complete): the sidebar search box and the chats-history-overlay
      filter are read-only client filters — they submit no payload.
    - Conversation rename is client-only state today; when persistence
      lands (future phase) the stored schema gets a matching `Field`
      bound (code note flags the spot).
    - Gate: **481 backend + cross-org isolation + mypy --strict** green;
      frontend tsc/eslint/build green; operator web-tested (login
      empty-field guidance, composer cap, rename revert, no
      `[object Object]`). Commits `<this>`.

**Exit criteria:** a fresh Wolf install (a) auto-creates Superuser
"Wolf" with a 32-char autogenerated password; (b) Superuser can
create Organizations + Users; (c) each user gets an invite link
that they paste while logged-in-and-on-network to unlock features;
(d) login UX requires only email + password (no org field); (e)
multi-org users see the org-switcher post-auth; (f) every config
change emits the appropriate audit event.

## Phase 6.6 — Superuser-owned Wazuh component mapping

**Per ADR 0020 (ACCEPTED 2026-06-10).** Sequenced AFTER Phase 6.5
so the Superuser + RBAC + per-tab header model is in place before
this UI uses it. 5 sub-slices, **3-5 sessions estimated**.

> **Sequencing note (2026-06-16, operator direction):** Phase 6.6 is
> proceeding **now, ahead of Phase 6 (the wolf-gateway Approval Gateway)**.
> The original table sequenced 6.6 after Phase 6 only because both touch
> wolf-server settings APIs — but 6.6 has **no functional dependency** on the
> gateway; it depends only on the shipped Phase 6.5 Superuser + RBAC + per-tab
> header model. Phase 6 remains designed-not-started and unblocked.

1. **6.6-a — Backend: install-level Wazuh ecosystem config** — ✅
   **SHIPPED 2026-06-16.** DB schema `wazuh_ecosystem_topology`
   (single-row, install-wide; migration 0011; DB-enforced singleton);
   API `GET / PUT /api/v1/superuser/wazuh-topology` (Superuser-only —
   path follows ADR 0020 + the existing `superuser` router convention,
   NOT the earlier `/api/v1/install/...` draft); reusable
   `wazuh/probe.py` (indexer/manager/dashboard) + pydantic discriminated
   union (`wazuh/topology.py`, single/distributed); **validate-before-
   persist HARD fail** (any blocker endpoint fails → save rejected;
   distributed workers are warnings); credentials → secrets backend only
   (ADR decision 7); audit `install.wazuh_topology.updated` /
   `…probe_failed` (system-level, never logs creds); "omit password ⇒
   keep existing". Backend-only + inert at runtime until 6.6-e wires it
   into the query path. 27 tests (13 probe via MockTransport, 14
   model+API); 476 backend / 0 skip green, `alembic check` clean +
   0011 round-trips. Commits `<this>`.

2. **6.6-b — UI: install-level Wazuh ecosystem page** — Superuser-
   only Settings → Wazuh Ecosystem page; Single/Distributed
   topology builder; per-endpoint probe results; hard-fail save
   if any endpoint probe fails.

3. **6.6-c — Backend: per-org Wazuh credentials refactor** —
   migrate existing `connection_profiles` to per-org credential
   model; API `GET / PUT /api/v1/organizations/{id}/wazuh-credentials`
   (Superuser-only; Admin/Engineer rejected at decorator); probe
   logic for per-org credentials (returns scope summary); soft-
   fail save when probe fails (so Superuser can save credentials
   before Wazuh-side admin provisions them).

4. **6.6-d — UI: per-org Wazuh credentials tab** — Superuser-only
   "Wazuh Credentials" tab within each org's settings; form +
   probe + save flow; rotation log display.

5. **6.6-e — Runtime: per-query credential + topology resolution** —
   update the Wazuh query path to read topology + credentials fresh
   per query; random-indexer-node routing for distributed
   deployments; end-to-end test from a chat query through the
   per-org credentials hitting the actual Wazuh ecosystem.

**Exit criteria:** Superuser configures Wazuh ecosystem topology
(single-host OR distributed) via the GUI; for each Organization,
Superuser configures per-org Wazuh API credentials; an Analyst in
that org chats with Wolf + Wolf successfully queries the org's
Wazuh data using the per-org credentials.

## Phase 6.7 — Notification infrastructure

**Per ADR 0021 (PROPOSED 2026-06-15), operator-requested.** A
dedicated, per-user **notification** feature — strictly **isolated
from audit/logging** (its own table/model; audit stays the
immutable compliance record, never coupled). Surfaces when an
operation touches a user:

- org Admin changes a user's **role** → that user is notified;
- Admin **resets** a user's password → that user is notified;
- the full **Superuser-access lifecycle** (requested / cancelled /
  approved / rejected / revoked / time-expired) → notify the
  relevant parties (requesting Superuser + the org's Admins /
  members as appropriate);
- org-related changes.

**v1 delivery** reuses the 6.5-f banner pattern (poll + on-action +
window-focus) — useful before any streaming transport exists. UI:
per-user feed + a notification bell. **Email delivery** rides on
Phase 6.9 (SMTP, ADR 0022), which lands first — so the bell can also
notify by email from day one.

## Phase 6.8 — Real-time push (SSE)

**Per ADR 0021 (PROPOSED 2026-06-15).** A server-sent-events
channel that pushes notification + Superuser-access-banner state
**live**, replacing the poll. The chat answer stream already uses
SSE, so the transport is familiar ground. Upgrades the 6.7
notification bell + the 6.5-f banner from poll → real-time without
changing their semantics.

## Phase 6.9 — Outbound email (SMTP)

**Per ADR 0022 (PROPOSED 2026-06-16), operator-requested.** Wolf
gains outbound system email. **Executes before 6.7** despite the
higher number, so the notification feature ships with an email
delivery channel rather than retrofitting one.

- **Wolf is an SMTP *client*, never an MTA** — it relays through an
  operator-configured, **provider-agnostic generic SMTP** endpoint
  (host / port / encryption / user / pass / from / reply-to).
  Recommended relays are **free-tier transactional ESPs** — Brevo
  (~300/day forever), SMTP2GO (~1k/mo), Resend / MailerSend (~3k/mo)
  — with Amazon SES or any paid SMTP as a drop-in later. Switching
  providers is config only, never code.
- **Deliverability is a documented contract:** the operator
  authenticates a sending domain with **SPF + DKIM + DMARC**; Wolf
  ships a **`wolf-mail doctor`** check that verifies those records
  and warns on misconfiguration. (Inbox placement is ~90% domain
  auth + reputation; self-hosting an MTA is what gets blocked.)
- **Architecture:** a `MailService` core + **`wolf-mail` shell
  wrapper** (shell-wrapper pattern); **web-first config** (Superuser
  dashboard, DB source of truth, CLI↔GUI synced, audited) with the
  SMTP password in the **secrets backend**; a durable `email_outbox`
  table (queue + retry + history) drained by an **in-process poller**
  (no broker dependency in v1); **Jinja multipart text+HTML
  templates** versioned in-repo.
- **First consumer:** verification/invite email — extends 6.5-h
  (and the future Superuser invite-link flow). Email **augments**
  the copy-link flow, never replaces it (copy-link stays the
  no-SMTP / air-gapped fallback). Reports = on-demand "email me
  this" first; scheduled digests later.
- **Audit vs notification:** email *sends* are audited
  (`email.sent` / `email.failed`) — a system action, distinct from
  ADR 0021's notification-isolation rule. Bounce handling: v1
  log-and-suppress; ESP-webhook ingestion as a fast-follow.
- **Security:** TLS required, creds in the secrets backend,
  header/CRLF-injection defense (server-resolved recipients), no
  open relay, per-org + global rate limits.

## Phase 6.10 — Superuser config-settings system (web ⇄ CLI ⇄ env sync)

The implementation of **ADR 0019** (web-first-configurability) for
runtime knobs, prompted 2026-06-16 by the same-network gate needing a
GUI toggle. Today config is **env-only** (`config.py` pydantic
settings) — there's no DB settings table, no config API, no config CLI,
and the dashboard `/settings` area is only `access` + `users`. This
phase builds the missing substrate:

- **DB as source of truth** for operator-settable knobs + a config API;
  a **Superuser Settings GUI page**; a **Wolf config CLI** (shell-wrapper
  pattern, `shell-wrapper-required-pattern` memory). All three surfaces
  (OS terminal/env ⇄ CLI ⇄ Web-GUI) stay **identical + synced**, every
  change **audited** (ADR 0019's GUI↔CLI-sync mandate).
- **Authorization model (operator-stated 2026-06-16):** *all* Wolf
  management/configuration is **Superuser-only**; org management →
  org admins; user settings → users; every config surface scoped to its
  role. Generalizes `wolf-bootstrap-superuser-flow`.
- **First consumer:** the **same-network gate** toggle (6.5-h.2 shipped
  the gate env-only + default-OFF; this turns it into a synced Superuser
  on/off switch). Other env knobs migrate in as catalogued by ADR 0019.
- **Follow-up (MSSP-correct gate):** **per-org trusted networks** — each
  org defines its own CIDRs; verification checks the user's IP against
  *their* org's networks (not the provider's). Resolves the MSSP gap the
  global gate can't (open question: Superuser-set vs org-admin-set).

Ordering: foundational enabler; slots when the configurable-surface
count justifies it (the gate toggle is reason enough to start). ADR
0019 already governs the design; a focused implementation ADR can follow
at phase-open if the data model warrants.

## Phase 7 — Cases and reporting (wolf-hunt foundation)

This was the original Phase 5 — bumped here because the
deployment-substrate work (current Phase 5.x sub-tree) was the
critical-path blocker for everything that comes after release.

Scope extended per ADR 0017 (2026-06-10): this phase delivers
the foundational case-management layer that **wolf-hunt** (Phase
9.5) builds the full incident-response platform on top of.

- Case data model — triggering signal, timeline, findings,
  proposals, communications, disposition.
- Auto-case creation on serious investigations; manual case
  creation.
- Case UI: timeline view, findings view, evidence appendix.
- Report templates: incident, executive, compliance, shift
  handover, threat-hunt.
- Templated, slot-filled report generation with grounding
  validation.
- Export to Markdown, HTML, PDF.

**Exit criteria:** an analyst can take an investigation from
question to closed case, produce a grounded incident report, and
the report opens cleanly in PDF.

## Phase 7.5 — Central Brain: memory + deep-think + self-validation

**Per ADR 0017 (ACCEPTED 2026-06-11).** Adds Wolf's cognitive
layer — the integrated memory + reasoning + self-validation
scaffolding that the underlying model runs inside.

Four subsystems:

- **Memory layer** — four memory types:
  - Episodic (in-conversation turns; existing `messages` table)
  - Session (per-conversation auto-summary; NEW
    `session_memory(conversation_id, summary, embedding, ...)`)
  - Long-term (cross-conversation operator facts; NEW
    `operator_memory(id, organization_id, user_id, fact_type, ...)`
    — `fact_type` enum: 6 categories — preference /
    environment_fact / runbook / social_context / observation /
    incident_lesson)
  - Semantic (environment knowledge graph; NEW
    `environment_entities` + `environment_edges` tables in Postgres;
    operator_memory + semantic both hard-partitioned by
    `(organization_id, user_id)` at the SQL layer)
  - Long-term confidence decay: exponential half-life (30d
    default); auto-prune at confidence < 0.1
  - Retention policy: per-fact-type defaults
    (preference/runbook/incident_lesson live until deleted;
    environment_fact + social_context 12mo; observation 90d)
  - Memory recording: always-on by default with per-user opt-out
- **Thinking layer** — new **deep-think** agent strategy
  alongside existing frontier / guided / pipeline (ADR 0001).
  5-step decomposition: decompose → per-sub-question RAG +
  grounding loop → synthesize → final grounding pass → confidence
  summary. **Triggers**: operator-explicit "Deep Think" button
  AND auto-escalation from Uncertain / Not Verified first-pass
  verdicts. **Cost cap**: soft (warning pill once threshold
  crossed in a conversation; default configurable per install).
- **Self-validation layer** — extends grounding validator (ADR
  0013) with: (a) **action validator** — LLM-as-judge runs BEFORE
  wolf-gateway approval; verifies target identity + blast radius
  + organization context + action-vs-conversation alignment; HARD
  GATE with no bypass + no cost cap (safety > perf); inline
  rejection reason + "Edit and retry" UX. (b) **3-state confidence
  calibration** — Confident+verified / Confident with caveat /
  Insufficient evidence. Honors operator point 8 via the §"Robust
  answer posture" three pillars (try harder → never abdicate
  without a next step → transparency over confidence theater).
- **"My memory" dashboard** — per ADR 0019: user-scoped + cross-
  org view (Alice sees ALL her memory across all her
  UserOrganization memberships, labeled by org). Read + delete
  capability (no edit — prevents operator gaslighting Wolf into
  false facts). Superuser-self-only at data-access: even with
  org-consent grants, no role can see another user's memory.

9 sub-slices (7.5-a through 7.5-i) per ADR 0017's implementation
sequencing.

**Exit criteria:** the model remembers what was discussed
yesterday, knows the operator's environment, can deep-think on
complex queries, and self-validates actions before sending them
to wolf-gateway.

## Phase 8 — Detection engineering and threat-hunt features

- `propose_rule_tuning` enhanced: produces a diff with an
  explanation.
- "Rule explorer" UI surface pairing `get_rule_definition` with
  the agent's explanation of what the rule does and the alerts
  it has fired.
- Threat-hunt mode: hypothesis-driven sessions, hunt reports.
- `lookup_ioc` + `enrich_geoip` enrichment tools wired to
  configurable threat-intel sources.

**Exit criteria:** detection engineer investigates a noisy rule,
proposes a tuning, and has it executed through wolf-gateway.

## Phase 8.5 — Central Brain: continuous learning workers

**Per ADR 0017 (ACCEPTED 2026-06-11).** Background workers that
make Wolf get smarter from the operator's environment over time.
Independent of the chat path; all write to the per-organization
knowledge corpus (RAG store) + semantic memory (Phase 7.5). One
worker invocation per organization, never a single job iterating
across organizations.

- **Knowledge feedback worker** — operator-reviewed case-close
  summaries auto-ingest into the organization's private corpus.
  (Originally Phase 10; consolidated here.)
- **Alert-pattern extraction worker** — periodic clustering of
  Wazuh alerts, surfaces recurring patterns, promotes them to
  semantic memory observations. **Cadence**: operator-configurable
  per org (default daily); via the ADR-0019 settings surface.
- **User feedback signal** — thumbs-up/down on Wolf's answers
  becomes a retrieval-ranking signal. Negative feedback weights
  down those chunks for similar future queries.
- **Environment fingerprinting worker** — auto at org bootstrap
  + periodic refresh (no opt-in). Walks the Wazuh API + indexer
  to enumerate:
  - Agents, hosts, rules, groups, network topology
  - **Wazuh log sources** (Round 4 operator scope expansion,
    2026-06-11): `alerts.json` (realtime manager alerts log),
    `archives.json`, manager logs (ossec.log + agent buffers),
    indexer-side indices (`wazuh-alerts-*`, `wazuh-monitoring-*`,
    `wazuh-statistics-*`)
  - **Log content NOT replicated** into Wolf's DB — the wazuh-
    indexer remains the canonical store. Wolf tracks log SOURCES
    as semantic-memory entities of type `log_source` + queries
    the indexer for real-time content via per-org Wazuh API
    credentials (per ADR 0020). Avoids storage explosion +
    duplication of the indexer's role.
  - Populates `environment_entities` + `environment_edges` with
    entity types: host / agent / user / rule / mitre_technique /
    network / service / cve / **log_source**.

**Exit criteria:** after one month of operation in an organization,
the per-org corpus contains org-specific observations + feedback-
tuned retrieval is measurably better at returning chunks the
operator marked helpful previously. Log sources are tracked + the
agent can query them via the indexer.

## Phase 9 — Playbooks and orchestration

- Playbook engine: named, versioned, step-by-step workflows with
  explicit checkpoints (`08`).
- Starter library of playbooks for common scenarios.
- Shift-handover report generated from open cases.
- Cross-case analytics dashboards (per-organization + MSSP-parent-scope).

## Phase 9.5 — wolf-hunt: Incident Response + Case Management platform

**Reserved per ADR 0017 (ACCEPTED 2026-06-11). Detailed design in
a future ADR (expected ~0021) at phase-open time.**

Builds on Phase 7's case data model. Adds a dedicated incident-
response platform within Wolf — separate dashboard + UI/UX,
accessible from wolf-dashboard.

Core capability: instead of one-case-per-alert (which produces
alert fatigue), wolf-hunt **correlates** related Wazuh alerts +
events into a single incident with:
- A timeline of all contributing alerts + events
- An attack-narrative summary (LLM-generated, grounding-checked)
- Suggested eradication / mitigation / containment steps
- Operator-facing case workflow (new / triaging / contained /
  closed) with audit
- Cross-referenced threat intel from wolf-den when available

**Out of scope of this roadmap entry**: the correlation algorithm
itself, the case schema, the dashboard UI. All deferred to the
wolf-hunt ADR when Phase 9.5 opens.

## Phase 10 — Knowledge feedback and growth

- Case-close summary: analyst-reviewed, structured.
- Auto-ingest of reviewed summaries into the organization's private
  corpus, with audit and reversibility.
- Operator controls over what auto-ingests and from where.
- Periodic re-evaluation of retrieval quality.

## Phase 11 — Integrations (ongoing, not gating)

- Notification adapters: Slack, Teams, email, webhook.
- Ticketing adapters: Jira, ServiceNow, webhook.
- Audit-log forwarding to external SIEM (including back to
  Wazuh as a separate index).

Land any time after Phase 6 has a working approval queue.

## Phase 11.5 — wolf-den: Cyber Threat Intelligence platform

**Reserved per ADR 0017 (ACCEPTED 2026-06-11). Detailed design in
a future ADR (expected ~0022) at phase-open time.**

Separate platform within Wolf — distinct dashboard + UI/UX,
accessible from wolf-dashboard. For threat hunters who want a
focused CTI surface vs the general agent-chat experience.

Core capabilities:
- IOC extraction from the operator's environment (file hashes,
  domains, IPs, registry keys, etc.) accumulated from Wazuh
  events
- Per-IOC observation tracking (where it appeared, how often,
  on which hosts, what rules fired)
- Threat-actor / campaign correlation against MITRE ATT&CK +
  external threat feeds
- Wolf-generated intel reports for the operator's environment
- Case creation from CTI findings (cross-references wolf-hunt)

**Out of scope of this roadmap entry**: the IOC schema, the
intel-report format, the external-feed integration points. All
deferred to the wolf-den ADR.

## Phase 12 — wolf-pack: native agents on Wazuh hosts

**Renamed from "Wolf Knowledge Relay" per operator direction
(ADR 0017, 2026-06-10).** Scope also expanded.

Native daemon (a "wolf-pack agent") deployed across every Wazuh
host — indexers, servers, dashboards, managers. Two
responsibilities:

1. **Inbound to Wolf** — ships rules, decoders, SCA findings,
   vulnerability data, asset inventory, agent-level health
   into wolf-server via mTLS (the original "Wolf Knowledge
   Relay" scope).
2. **Outbound from Wolf** — executes actions Wolf can't reach
   from the brain host: local-only commands, host-specific
   diagnostics, container-bound tasks, custom scripts under
   the wolf-gateway approval flow.

Hard dependency on Phase 5.4 HTTPS + Phase 5.6 mTLS (both
delivered) + Phase 6 wolf-gateway (for the outbound command
flow). Detailed design in a future ADR (expected ~0023) at
phase-open time.

## Phase 13 — Optional auto-execution

Only consider after the platform has months of safe operation +
data showing the agent's proposals are consistently sound.
Conditions defined in `04`. Default off, opted-in per organization,
narrowly scoped, circuit-broken, fully audited.

---

## Phase ordering — divergence from the original plan

The original roadmap (this file pre-2026-06-04) had:

| Original | What it described |
|---|---|
| Phase 5 | Cases and reporting |
| Phase 6 | Propose tools / Approval Gateway |
| Phase 7 | Detection engineering / threat-hunt |
| Phase 8 | Playbooks and orchestration |
| Phase 9 | Knowledge feedback and growth |
| Phase 10 | Integrations |
| Phase 11 | Auto-execution |

What actually happened: between Phase 4 close and starting on the
original Phase 5, the team realised the deployment story
(systemd, FHS, packaging, multi-component architecture) needed to
land BEFORE Cases and reporting could be developed against a
realistic install. So Phase 5 was reshaped into the deployment
sub-tree (5.0c UI polish + 5.4–5.10 infrastructure). The original
Phase 5 (Cases and reporting) moved to Phase 7. Everything else
shifted by +1.

The post-Phase-5.10 ordering reflects that shift. ADRs that
reference "Phase 6" continue to mean the Approval Gateway
(unchanged from the original numbering).

### 2026-06-10 / 2026-06-11 — multi-organization design arc added

Four ADRs ACCEPTED in this window added a tightly-coupled set of
sub-phases between the existing Phase 6 work and Phase 7:

| Phase | Driver | Why this position |
|---|---|---|
| **6.4** | ADR 0018 | tenant → organization codebase rename. Pre-req for every Phase 6.5+ slice that references the new naming. Single PR, ~1-2 sessions. |
| **6.5** | ADR 0018 | Bootstrap Superuser + Per-Org RBAC + Login UX. 9 sub-slices, ~12-13 sessions. Land BEFORE Phase 7's case-management work since cases attach to an organization + a user with a role. |
| **6** | (existing) | Wolf-gateway — the Approval Gateway. After 6.5 because the gateway uses 6.5's role-decorator pattern + needs the organization + role model from 6.5-b. |
| **6.6** | ADR 0020 | Superuser-owned Wazuh component mapping. After Phase 6.5 because the UI requires the Superuser identity + per-tab header model. Sequenced after Phase 6 because it touches the same wolf-server settings APIs. |

This is a meaningful expansion (~16-19 sessions across 6.4 + 6.5
+ 6.6) but unblocks everything downstream. With these in place,
Phases 7, 7.5, 8, 8.5, 9, 9.5+ all build against the
multi-organization-ready foundation.

---

## Things to deliberately defer (or never build)

- **Autonomous action without human approval** — the foundational
  scope decision.
- **Log mutation** — never a capability, not even as a proposal.
- **Replacing Wazuh** — Wolf augments, never replaces.
- **General-purpose chat** — keep the agent scoped to security
  operations.
- **A model marketplace, billing, usage metering** — the platform
  is free and open-source; cost transparency yes, metering no.

---

## Quality gates that apply to **every** phase

These are non-negotiable; the coding agent must enforce them at
all times.

1. **Strict typed schemas** on every tool input and output.
2. **Organization context injected by wolf-server**, never read from the
   model.
3. **Capability tiers enforced** by the registry and dispatch
   logic.
4. **Audit every event** that matters.
5. **No execute tool in wolf-server**; they exist only in
   wolf-gateway.
6. **The cross-organization isolation test suite must pass** in CI for
   any change to touch main.
7. **Grounding validator runs** on any final answer that makes
   factual claims.
8. **Every model call works on at least one local model** in CI
   — proving the "no paid dependency required" promise stays
   true throughout.
9. **The three pre-push smokes pass locally + in CI** —
   `make smoke-mtls` (Phase 5.6-e), `make smoke-database`
   (Phase 5.7-d), `make smoke-systemd` (Phase 5.8-d).
10. **Integrity across the stack** — every change preserves
    integrity across frontend / backend / DB / libraries / UI;
    full backend suite + cross-organization gate on every services/
    change.
11. **No unaddressed errors** — never leave errors / warnings /
    silent diagnostics unaddressed; "pre-existing baseline" is
    not a pass; fix or track-with-plan.
12. **Periodic plan-sync** — between major phase transitions,
    audit the roadmap + architecture docs + ADRs + PROGRESS for
    drift vs shipped work; surface findings proactively.
