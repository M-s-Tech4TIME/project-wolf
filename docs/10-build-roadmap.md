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
started.** Sub-phase ordering 6.4 → 6.5 → 6 → 6.6 → 7 → 7.5 → 8 →
8.5 → 9 → 9.5 → 10 → 11 → 11.5 → 12 → 13. Reflects the ADR-
derived sequencing; see `## Phase ordering — divergence from the
original plan` below.

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
   Transitional JWT-claim fallback when the header is absent
   (removed with c-ii, together with login's organization_id
   field). Login gained the ADR's three-shape response
   (Superuser+redirect / auto-selected / needs_org_selection
   with memberships; zero-memberships → 401 contact-your-admin;
   inactive orgs excluded). New audit-recording
   `POST /auth/select-organization` + `/auth/switch-organization`;
   `/me` reflects the header org (per-tab profile chip). 14 tests
   incl. the two-tabs-two-roles workflow.

5. **6.5-c-ii — Frontend login + per-tab org state** — dashboard
   removes org field from login form; handles three login
   responses (Superuser → `/superuser/dashboard`; auto-selected
   → `/chat`; needs-org-selection → org-switcher); per-tab
   `sessionStorage` for active `organization_id`; every API call
   sets `X-Organization-Id` header.

6. **6.5-d — Organizations + Superuser-dashboard UI** — Superuser-
   only `/superuser/dashboard` route; Organizations page
   (list/create/edit/delete); per-org page with initial Admin user
   creation; install-wide audit-log view.

7. **6.5-e — User management UI (per-org)** — Org-Admin-only
   Users page within each org; role assignment dropdowns; audit-
   event display for role changes.

8. **6.5-f — Superuser-membership-grant flow + UI** — backend
   Superuser-request → Admin-approve/reject → time-limited
   membership grant (default 24h expiry); revoke (Admin-initiated
   or expiry-driven); UI on both Superuser side ("Request access
   to <org>") and Admin side (pending requests in Settings →
   Access); org member notifications.

9. **6.5-h — Invite-link verification flow + same-network gate** —
   `User.verification_status` enum + verification token; Admin
   generates + copies invite link via dashboard (no SMTP);
   verification gate on every authenticated endpoint; dynamic
   same-network detection (Wolf enumerates own NICs per-request +
   matches source IP against any CIDR Wolf is on).

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
this UI uses it. 5 sub-slices, **3-5 sessions estimated**:

1. **6.6-a — Backend: install-level Wazuh ecosystem config** — DB
   schema `wazuh_ecosystem_topology` (single-row, install-wide);
   API `GET / PUT /api/v1/install/wazuh-topology` (Superuser-only);
   probe logic for all endpoints; audit-event emission on topology
   change.

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
