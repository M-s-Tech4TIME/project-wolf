# Wolf — Development Progress

> **This is the live state of the Wolf project.** Read this file at the start
> of every Claude Code session, before doing anything else. Update it at the
> end of every session.
>
> For history of what changed when, see `CHANGELOG.md` (append-only).

**Last updated:** 2026-05-22 (second session of the day) by claude-code

---

## 1. Where we are right now

**Current phase:** Phase 2 — Read path, end to end (per `docs/10-build-roadmap.md`).

**Phase status:** Model abstraction layer complete (Phase 1). Read path
foundation, agent loop with three strategies, and Next.js 16 frontend
all built and operational. 9 read tools registered with the dispatcher;
5 of 9 verified against the user's real Wazuh deployment, 4 of 9
mock-only. **Dev default flipped from `llama3.2` to `qwen3:4b` per ADR
0004** (probe-grounded decision; Apache 2.0 license; +0.07 overall
probe score; same strategy tier).  Chat path re-verified end-to-end on
qwen3:4b in `guided` mode (one tool call → `count_alerts_by_severity`,
grounded cited answer).

**Phase 2 exit criteria progress** (from `docs/10-build-roadmap.md`):
- [x] Wazuh OpenSearch client with forced tenant filter (opt-in per tenant)
- [x] Wazuh Server API client (read endpoints only)
- [x] Tool registry with strict input/output Pydantic schemas
- [x] First read tools: 9 of 9 implemented (5 verified live, 4 mock-only)
- [x] Agent loop with three strategies (frontier / guided / pipeline)
- [x] Resource guardrails (time window, result count, per-tenant rate limit)
- [x] Audit logging on every model call and every tool call
- [x] Minimal UI: login, tenant picker, ask question, see cited answer
- [ ] Analyst question end-to-end on **both** a frontier model AND a local
      Ollama model — currently only verified on local Ollama (`llama3.2`)
      against the real Wazuh; frontier-API confirmation pending.

---

## 2. What's currently built and working

Status legend: ✅ working, 🟡 partial, ❌ broken/disabled, ⏳ planned only.

### Orchestrator (`services/orchestrator/`)
- ✅ FastAPI app, lifespan-driven Alembic migrations on startup
- ✅ Auth: bcrypt local accounts, JWT HS256 cookies, OIDC adapter stub
- ✅ Immutable `TenantContext`, AuthMiddleware, append-only audit log
- ✅ Model abstraction layer (`app/models/`): Anthropic, OpenAI, Ollama adapters (httpx-based, no SDK deps)
- ✅ `CapabilityDescriptor` + `KNOWN_MODELS` registry
- ✅ Tool registry + dispatcher (`app/tools/`): tier enforcement,
      Pydantic input/output validation, audit on every branch
- 🟡 9 read tools registered (`app/tools/registration.py`):
    - ✅ verified live: `search_alerts`, `aggregate_alerts`,
          `count_alerts_by_severity`, `list_agents`, `get_cluster_health`
    - 🟡 mock-only (httpx mocked, not yet hit live Wazuh):
          `get_event_timeline`, `get_agent_alert_history`,
          `get_agent_detail`, `get_rule_definition`
- ✅ Agent loop with three strategies (`app/agent/`): frontier / guided /
      pipeline; `LoopEvent` emission for SSE; multi-turn `history` support
- ✅ Endpoints: `POST /api/v1/auth/{login,logout}`, `GET /me`,
      `GET /me/tenants`, `POST /api/v1/chat`, `POST /api/v1/chat/stream`
- ✅ Per-tenant Wazuh resolver + secrets backend (encrypted-file)
- ✅ Bootstrap CLI (`app.management.bootstrap_tenant`) and smoke-test CLI
      (`app.management.smoke_wazuh`)

### Gateway (`services/gateway/`)
- ⏳ Not started. Stub package only. Per the architecture, execute tools
      live here exclusively (Phase 4+ work).

### Frontend (`frontend/`)
- ✅ Next.js 16 (Turbopack) + React 19 + Tailwind 4
- ✅ shadcn/ui primitives, Lucide icons
- ✅ Auth flow: login page, cookie-credentialed fetch, protected routes
- ✅ Tenant switcher (consumes `/me/tenants`)
- ✅ Multi-turn conversations: sidebar shows conversations, message thread
      replays the active conversation, `history` sent with every submit
- ✅ SSE streaming: consumes `/api/v1/chat/stream`, renders LoopEvents
      (tool calls, citations) live
- ✅ Markdown rendering for assistant answers (react-markdown + remark-gfm)
- ✅ Citations panel
- ✅ `randomId()` fallback for HTTP / non-localhost contexts

### Shared packages (`packages/`)
- ✅ `common/wolf_common/`: structlog JSON logging, OpenTelemetry tracing,
      error taxonomy
- ✅ `secrets/wolf_secrets/`: abstract `SecretsBackend` protocol,
      Fernet-encrypted file backend
- ✅ `schema/wolf_schema/`: canonical types (`ToolSchema`, `ToolCall`,
      `ToolResult`, `ToolTier`, `CapabilityDescriptor`, `ChatRequest`,
      `ChatResponse`, `Message`)

### Tooling (`tools/`)
- ✅ `model_probe/`: built in Phase 1; 12 unit tests passing;
      **probed live against `llama3.2`, `qwen3:4b`, `gemma3:4b` on this
      hardware on 2026-05-22** — see ADRs 0001/0002/0003.  sys.path
      bootstrap added to `__main__.py` to resolve the two-`app/`-packages
      collision that blocked the CLI invocation (commit `e9cc316`).
- ⏳ `tenant_isolation_test/`: stub only; the live isolation tests live in
      `services/orchestrator/tests/test_cross_tenant_isolation.py`
- ⏳ `seed_knowledge/`: stub only (Phase 3 RAG work)

### Infrastructure
- ✅ Postgres 17 + pgvector on `localhost:5432`
- ✅ Ollama on `localhost:11434` with `llama3.2:latest` (3B, Q4_K_M, ~2 GB)
- ✅ User's real Wazuh on `192.168.76.129` (Indexer :9200, Server API :55000,
      self-signed TLS)
- ✅ CI workflow (lint / typecheck / test / safety-check / local-model-check)
- ❌ Docker Compose stack: not the current dev path; services run as
      foreground / `nohup` processes
- ❌ Keycloak / OpenBao: not yet up — local accounts + encrypted-file
      secrets are the current dev path

---

## 3. Current configuration

**Dev environment:**
- Host: VM at `192.168.76.128`, ~16 GB RAM, CPU-only (no GPU detected by Ollama)
- OS: Ubuntu 24.04.4 LTS (Noble Numbat)
- Python: 3.13 (pinned in `.python-version`, managed via `uv`)
- Node: 24 LTS (pinned in `.nvmrc`); installed locally at `~/.local/node24`
- `npm`: 11.6.1

**Model defaults** (in `services/orchestrator/app/config.py`):
- `DEFAULT_MODEL_PROVIDER`: `ollama`
- `DEFAULT_MODEL_ID`: **`qwen3:4b`** (switched from `llama3.2` on
  2026-05-22 per ADR 0004; Apache 2.0 license)
- `OLLAMA_BASE_URL`: `http://localhost:11434`
- Adapters active: Anthropic, OpenAI, Ollama
- `llama3.2` remains in `KNOWN_MODELS` for operator opt-in via
  `DEFAULT_MODEL_ID=llama3.2`.

**Wazuh connection** (per `TenantWazuhConfig` for tenant `acme`):
- Indexer: `https://192.168.76.129:9200` (self-signed; `verify_tls=False`)
- Server API: `https://192.168.76.129:55000`
- Credentials: in encrypted-file secrets backend at `.local/secrets.enc`
- `inject_tenant_filter=False` (standalone Wazuh deployment, no per-doc tenant_id)

**Service ports (dev, bound `0.0.0.0` for LAN access):**
- Orchestrator: `8000`
- Frontend: `3000`
- Ollama: `127.0.0.1:11434`
- Postgres: `5432`
- Gateway: `8001` (not yet running)

**CORS allow-origins:** `http://localhost:3000,http://127.0.0.1:3000,http://192.168.76.128:3000`

---

## 4. What's next

**Immediate next steps** (in priority order):
1. Wire the 4 remaining read tools (`get_event_timeline`,
   `get_agent_alert_history`, `get_agent_detail`, `get_rule_definition`)
   to real Wazuh.  Currently mock-only.
2. Verify the Phase 2 exit criterion against a frontier API model in
   addition to the local-Ollama path that already works.  Blocked on
   an Anthropic or OpenAI key in the secrets backend.
3. Batch-amend the remaining static `KNOWN_MODELS` entries to reflect
   measured capability — `llama3.2` (mid → mid match, but
   structured_output should go prompt_coaxed → unreliable per
   ADR 0001) and `gemma3:4b` (downgrade native_tool_calling to none
   per ADR 0003).  qwen3:4b already amended in commit `14cc727`.
4. Begin Phase 3 (RAG + grounding validator) per `docs/06` and
   `docs/10` — note that qwen3:4b's grounding-discipline probe
   failure makes the grounding validator higher priority.

**Blocked / waiting:**
- Frontier-API verification needs an Anthropic or OpenAI key in the
  configured secrets backend (not blocking dev, only the formal exit check).

**Deferred** (deliberately not doing now):
- Phase 3 (RAG + grounding validator) — pending Phase 2 close-out.
  qwen3:4b's grounding-fabrication probe result makes Phase 3 *more*
  important if/when qwen becomes the default, not less.
- Phase 4 (gateway service + propose/execute tools) — structural, separate
  service; not until Phase 2 ships.
- Docker Compose stack as the primary dev path — current `nohup` flow is
  fine; revisit when adding more services.
- Refactor of the two-`app/`-packages collision (services/gateway/app/ and
  services/orchestrator/app/ both named `app`).  The probe sys.path
  bootstrap works around it; a deeper fix (rename one) is larger surgery.

---

## 5. Active decisions and open questions

Things that need a human call before they can proceed. Move resolved items
to `CHANGELOG.md` as ADRs.

- [x] **Switch `DEFAULT_MODEL_ID` from `llama3.2` to `qwen3:4b`** —
      resolved in ADR 0004 (commit `e092e21`) + config flip
      (commit `ca495df`) + KNOWN_MODELS amendment to match measured
      probe capability (commit `14cc727`).  Verified end-to-end via
      curl: guided strategy, one tool call, grounded answer.
- [ ] Whether `count_alerts_by_severity` should remain a standalone tool
      or be folded into `aggregate_alerts` with a `bucket_by_severity`
      mode. Currently both registered; the prompt routes severity
      questions to the new one.

---

## 6. Known issues and tech debt

- Llama 3.2 on CPU-only inference is slow (~30-60s for first token cold
  start). Functional but a real UX limit; switching to `qwen3:4b` would
  also benefit here.
- Small-model fabrication: `llama3.2` occasionally embellishes details
  beyond what the tool returned. Phase 3's grounding validator is the
  designed solution.
- `services/orchestrator/app/tools/cluster.py` `manager_healthy` flag
  trusts the API responding == healthy; doesn't probe deeper signals.
  Adequate for Phase 2.

---

## 7. Test coverage status

- **128 backend tests passing** (`pytest services/orchestrator/tests packages/`)
- **0 failures**, **0 skipped**
- ruff: clean across the workspace
- mypy strict: 33 source files clean
  (`packages/{common,secrets,schema}/*` and
  `services/orchestrator/app/{tenancy,audit,wazuh,guardrails,agent}`)
- Cross-tenant isolation suite: in
  `services/orchestrator/tests/test_cross_tenant_isolation.py`, runs as
  part of the main suite. All 4 negative tests pass.
- Frontend: `next build` clean, `next lint` clean. No frontend test
  framework wired yet — deferred.
- CI: configured (`.github/workflows/ci.yml`) but not yet run against a
  remote (the repo's `main` is ahead of `origin/main` by 8 commits as of
  this session start).

---

## 8. Documentation status

- Planning bundle (`docs/00-13`): in git as of commit `c05cdce` (today).
- `docs/14-model-recommendations.md`: in git as of commit `b093761` (today).
- `docs/11-claude-code-instructions.md`: updated this session with the
  relaxed session-continuity protocol (reading required only for new env /
  new session / different model; end-of-session update remains mandatory).
  In git as of commit `b093761`.
- ADRs in `docs/decisions/`: 4 ADRs — 0001 (`llama3.2` baseline), 0002
  (`qwen3:4b`), 0003 (`gemma3:4b`), 0004 (default-model switch decision).
  README index in place.
- API docs: FastAPI auto-generates at `http://localhost:8000/docs`.
- README: in git as of commit `c05cdce`.

---

## 9. Hand-off note for next session

Phase 2 is functionally complete: a chat session against the user's real
Wazuh (192.168.76.129) on `llama3.2`/Ollama returns grounded answers with
citations through the Next.js frontend (`http://192.168.76.128:3000`).
Multi-turn works. Markdown renders. The new `count_alerts_by_severity`
tool gives correct severity breakdowns end to end.

**The default-model switch is done.** `DEFAULT_MODEL_ID` is `qwen3:4b`
(Apache 2.0).  ADR 0004 captured the reasoning; commits `e092e21`
(ADR), `ca495df` (config flip), `14cc727` (KNOWN_MODELS amendment to
match measured capability) form the audit trail.  End-to-end
re-verified: chat against the user's real Wazuh on `192.168.76.129`,
qwen3:4b in `guided` mode, one tool call to `count_alerts_by_severity`,
grounded cited answer ("15 alerts total, all low severity").

**Single most important thing for the next session to know:** the
mock-only read tools (`get_event_timeline`, `get_agent_alert_history`,
`get_agent_detail`, `get_rule_definition`) are the next Phase 2 close-
out — each is a few lines, but they need exercising against the real
Wazuh to flip from 🟡 to ✅ in Section 2.  After that the only
remaining Phase 2 item is frontier-API verification, which is blocked
on the operator providing an API key.

Operational note: services run as `nohup` background processes (not
systemd / compose).  On host reboot you must restart Ollama, the
orchestrator, and the frontend by hand.  Orchestrator needs the env
vars in Section 3; the canonical bundle lives at `/tmp/orchestrator.env`.
