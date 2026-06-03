# Wolf — Development Progress

> **This is the live state of the Wolf project.** Read this file at the start
> of every Claude Code session, before doing anything else. Update it at the
> end of every session.
>
> For history of what changed when, see `CHANGELOG.md` (append-only).

**Last updated:** 2026-06-03 by claude-code (Phase 5.6-c SHIPPED; 5.6-d–e remaining)

---

## 1. Where we are right now

**Current phase:** Phase 5.6 — Edge-component architecture + mTLS
(per ADR 0016).

* **Slice 5.6-a — Reverse-proxy route handler** — SHIPPED
  2026-06-03. wolf-dashboard's `app/api/[...path]/route.ts`
  catch-all forwards every browser `/api/v1/...` request to
  wolf-server. Browser only sees one origin (`:3000`). Cross-
  origin NetworkError eliminated. Verified live: GET/POST/SSE
  all pass through, multi-`Set-Cookie` preserved (both
  `wolf_access_token` + `wolf_refresh_token` flow through),
  token-by-token streaming flushes per chunk (no buffering).
* **Slice 5.6-b — `dashboard-client` cert via wolf-cert** —
  SHIPPED 2026-06-03. `wolf-cert init` now mints three leaves:
  `server` (SERVER EKU), `dashboard` (SERVER EKU), and the new
  `dashboard-client` (**CLIENT EKU**, CN = `wolf-dashboard-client`)
  at `.local/certs/dashboard-client/{cert,key}.pem`. Phase 5.6-c
  will require this cert at wolf-server's TLS boundary.
* **Slice 5.6-c — mTLS middleware on wolf-server** — SHIPPED
  2026-06-03. Three pieces shipped together:
  (1) wolf-server launcher passes `ssl_ca_certs=<Wolf CA>` +
  `ssl_cert_reqs=CERT_OPTIONAL` to uvicorn when the certs exist,
  so the TLS layer accepts and verifies any presented client cert
  against the Wolf CA; (2) a small monkey-patch on uvicorn's
  `RequestResponseCycle.__init__` surfaces the verified peer cert
  into `scope["state"]["wolf_peer_cert"]` (uvicorn 0.47 doesn't
  expose it natively); (3) `MtlsMiddleware` enforces the
  CN allowlist (default `["wolf-dashboard-client"]`), audit-logs
  every accept/reject, returns JSON 401 on policy violations, and
  bypasses GET /healthz from loopback so ops tools can probe
  without distributing the client cert. The dashboard's reverse-
  proxy Agent (5.6-a's `WOLF_DISPATCHER`) was extended to load
  `.local/certs/dashboard-client/{cert,key}.pem` into `Agent({
  connect: { ca, cert, key } })` — the proxy now presents the
  client cert on every outbound call. Verified live: no-cert
  → 401 mtls_required, with-cert → 200, /healthz from loopback
  no-cert → 200; full dashboard login + chat-stream round-trip
  works end-to-end with mTLS active.
* **Slices 5.6-d / 5.6-e** — launcher wiring polish + operator-doc
  walkthrough + 401-without-cert smoke test as a recurring
  integrity check.

APT / DNF packaging (Phases 5.9 / 5.10) remain deferred to the
official-release phase per the 2026-06-03 operator direction.

**Phase 5.5 — Component renaming refactor — CLOSED 2026-06-03.**
Pure refactor, zero functional change. The repo now matches ADR
0016's component naming end-to-end:

* `frontend/` → `services/dashboard/` (Next.js — the wolf-dashboard component)
* `services/orchestrator/` → `services/server/` (FastAPI — the wolf-server component)
* `services/orchestrator/app/` → `services/server/wolf_server/` (Python package — fixes Gotcha #1's two-app collision permanently)
* `services/gateway/app/` → `services/gateway/wolf_gateway/` (matches the wolf-gateway naming)
* `wolf-cert init` mints leaves named `server/` + `dashboard/` (was `orchestrator/` + `frontend/`)
* Server-side env vars / config defaults aligned (`TLS_CERT_PATH` defaults to `.local/certs/server/`)
* Dashboard env var renamed: `NEXT_PUBLIC_ORCHESTRATOR_URL` → `NEXT_PUBLIC_SERVER_URL`

Five commits, in order: initial 184-file rename (`a3d18ec`),
operator-tooling audit (`70d2d94`), exhaustive every-file audit
(`ad4868c`), three trailing references caught on re-read
(`0e428bc`), and the **total-rename closeout** sweep A→G
(`08dee03`) closing every remaining stale reference, including
one shipped CLI bug (`wolf-cert --leaf` help advertising leaf
names that no longer existed), the `package-lock.json` name
field, six dead `_ORCH = "services/orchestrator"` `sys.path`
bootstrap blocks (`tools/embedding_benchmark/*`, `tools/
seed_knowledge`, `tools/tenant_isolation_test`, `services/server/
tests/test_seed_knowledge_ingesters.py`), 14 broken `services/
server/app/…` markdown links in `ONBOARDING.md`, ~30 in-source
comments narrating current behaviour with old names (including
the LLM-visible system prompt's "the orchestrator stamps tenant
scope" rule), and shipped-package docstrings in `wolf_cert`,
`wolf_secrets`, `wolf_gateway`. Final gate: mypy 0 / ruff clean
/ tsc 0 / eslint clean / 311 backend tests / 6/6 tenant-isolation.

The planning bundle (`docs/00`–`docs/16`) deliberately retains
its pre-rename language as descriptive specs — see §6 below.

**Phase 5.4 — Native HTTPS + `wolf-cert` CLI — CLOSED 2026-06-03.**
Five sub-slices shipped between 2026-06-02 and 2026-06-03:
* 5.4-a (`9a44b65`) — `wolf_cert` library (CA generation, leaf
  signing, PEM I/O with strict permissions, status parsing) + 24
  tests. Workspace package shipped with `py.typed` for downstream
  mypy. `LeafKind.CLIENT` hook in place for the future relay
  phase.
* 5.4-b (`80e0f10`) — `wolf-cert` CLI dispatcher (`init` / `status`
  / `export-ca` / `add-host` / `renew` / `revoke`) + 21 tests.
  Console-script entry point + `python -m wolf_cert` module form.
* 5.4-c (`5afd4e9`) — Orchestrator HTTPS auto-detect launcher
  (`python -m app`) with pure-function `resolve_tls()` + 6 tests.
  Cert files themselves are the signal — no env flag.
* 5.4-d (`c7fed44`) — Frontend HTTPS auto-detect via
  `scripts/dev.mjs`. Same posture as orchestrator; mirrors the
  cert-files-are-the-signal contract.
* 5.4-e (`b064b82`) — `ONBOARDING.md` per-OS trust-install
  walkthrough; chain verified via `openssl verify`.

End-to-end verified: `wolf-cert init` flips both servers to HTTPS
(login HTTP 200 with TLS verify_result = 0 against the freshly-
minted Wolf CA); `wolf-cert revoke --yes` drops back to HTTP
automatically.

**Phase 5 prep (the 5.0a → 5.0c series) — CLOSED 2026-06-02.** The
chat UI now matches the Claude/ChatGPT class of interactions:
progressive token-by-token rendering, narrated activity feed,
concurrent per-conversation streams with a Stop button, full
conversation-tree branching (Edit / Retry with `< N/M >` navigator),
chats history pane with full-text search across every branch.

The 5.0c series itself shipped as: c-a (four-chip grounding +
verdict rename), c-b (layout overhaul + resizable Evidence panel),
c-c (Platinum / Dusk Blue / Steel Blue / Icy Blue palette), c-d
(progressive answer rendering — Ollama `stream:true` +
`model.delta` SSE), c-e (live activity feed), c-f + c-g (polish
backlog + retry-nudge + English-only), c-h (async stream
lifecycle + immediate sidebar slot), c-i + i.2 → i.5 (conversation
rename + polish wave + native delete dialog + Markdown polish),
c-j (chats history pane with full-text search), c-k (Stop button +
concurrent per-conversation streams), c-l (conversation tree
branching). Two cross-cutting commits landed in the same window:
typing-foundation fix (`bf00c01` — Phase-0 PEP-561 blind spot
closed, mypy 56 → 0) and IP-agnostic local access (`a3fdd73` —
stops the LAN-IP-rotation paper-cut). One feature tried and
removed in the same window: in-conversation Find (six iteration
passes, then reverted at user's request — too fragile a DOM-
injection interaction with the surrounding scroll machinery; full
narrative in CHANGELOG 2026-05-31).

**Standing rules active across the project** (cross-session memory):
- *Integrity across the stack* (2026-05-30) — every change preserves
  integrity across frontend / backend / DB / libraries / UI; full
  backend suite + cross-tenant gate on every `services/` change.
- *Quality + secure coding discipline* (2026-05-31) — features-first;
  quality + secure coding applied inline as each slice is built;
  dedicated hardening + audit pass deferred to a later phase but
  tracked, never abandoned.
- *No unaddressed errors* (2026-06-01) — never leave errors /
  warnings / silent diagnostics unaddressed; "pre-existing baseline"
  is not a pass; fix or track-with-plan, never just report-and-move-on.

**Phase 4 — multi-tenancy hardening — CLOSED 2026-05-27.** Four slices
shipped: two-tenant live DB + RAG isolation tests (4.1, `338413f`),
`bootstrap_tenant` validates + `--update` flag (4.2, `1da9e1c`),
`TenantScopedCache` + agent_name caching + audit-write isolation
(4.3, `3ff751c`), and the runnable `tools/tenant_isolation_test` live
smoke + ONBOARDING gotchas + close-out (4.4). Live isolation suite:
6/6 checks pass against the dev two-tenant state.

**Phase status:** **Phase 3 shipped end-to-end** (Slices 1, 1.5, 2A, 2B,
and 3). Phase 2 closed (ADR 0005). Phase 3 vertical:
RAG-over-real-corpus integrated into the agent loop with hybrid
retrieval + grounding validator surfacing inline `[unverified]`
markers on unsupported claims. Slice 3 added the production-grade
ingesters under `tools/seed_knowledge/`: MITRE ATT&CK STIX (697
techniques, matrix v19.1) and the Wazuh ruleset XML (4473 rules from
v4.9.2). The dev DB now carries **5170 shared chunks + 3
tenant-private** = 5173 total. `make check` 174 passed (128 prior +
19 knowledge + 16 validator + 11 ingester tests). End-to-end verified
against a brand-new dedicated agent at 192.168.245.129
(`linux-test-agent`, id 001): SSH brute-force triggered 9× rule 5710
+ 1× rule 5712 in Wazuh, Wolf chat investigated with 3 tool calls
(`search_alerts` + `get_rule_definition` + `query_runbook`) fusing
live Wazuh data with retrieved ATT&CK + ruleset documentation; the
grounding validator caught a false-negative claim in one run
(marked `[unverified]`) and degraded gracefully when the judge LLM
returned malformed JSON on a harder run.

**Phase 2 exit criteria progress** (from `docs/10-build-roadmap.md`):
- [x] Wazuh OpenSearch client with forced tenant filter (opt-in per tenant)
- [x] Wazuh Server API client (read endpoints only)
- [x] Tool registry with strict input/output Pydantic schemas
- [x] First read tools: **9 of 9 verified live** against real Wazuh
- [x] Agent loop with three strategies (frontier / guided / pipeline)
- [x] Resource guardrails (time window, result count, per-tenant rate limit)
- [x] Audit logging on every model call and every tool call
- [x] Minimal UI: login, tenant picker, ask question, see cited answer
- [x] Analyst question end-to-end on **both** a frontier model AND a local
      Ollama model.  Local-Ollama: `qwen3:4b` in `guided` mode, ~76s
      cold, grounded cited answer.  Frontier-API: `nvidia/nemotron-3-
      super-120b-a12b:free` via OpenRouter in `frontier` mode, 17s,
      structured "Answer / Evidence / Citations" reply.  Both verified
      against the operator's real Wazuh on the same day (ADR 0005).

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
- ✅ 9 Wazuh read tools + 1 Phase-3 RAG tool registered
      (`app/tools/registration.py`):
      `search_alerts`, `aggregate_alerts`, `count_alerts_by_severity`,
      `get_event_timeline`, `get_agent_alert_history`, `list_agents`,
      `get_agent_detail`, `get_rule_definition`, `get_cluster_health`,
      **`query_runbook`** (Phase 3 Slice 1, added 2026-05-24).
- ✅ Phase 3 knowledge layer (`app/knowledge/`): `EmbeddingProvider`
      protocol + two adapters — `OllamaEmbeddingAdapter`
      (nomic-embed-text, 768-dim, default) and
      `SentenceTransformersEmbeddingAdapter` (BGE-base-en-v1.5,
      opt-in via the `embeddings-local` extra; recorded in ADR 0012);
      `make_embedding_provider` factory selects via env
      (`EMBEDDING_PROVIDER=ollama|sentence-transformers`).
      `KnowledgeStore` protocol + `PgvectorKnowledgeStore` (tenant-
      scoped retrieval enforced at the SQL clause); `KnowledgeChunk`
      SQLAlchemy model with `chunk_metadata` JSONB + `embedding`
      `Vector(768)` + `embedding_model` stamp for re-embedding triggers.
      HNSW cosine-distance index per doc 06.
- ✅ Embedding-stack benchmark CLI (`tools/embedding_benchmark/`):
      side-by-side cold-start / per-query latency / corpus-throughput /
      qualitative top-5 retrieval comparison between both adapters
      against the seeded dev corpus.  Re-runnable for future
      empirical evaluations.
- ✅ Agent loop with three strategies (`app/agent/`): frontier / guided /
      pipeline; `LoopEvent` emission for SSE; multi-turn `history` support
- ✅ Endpoints: `POST /api/v1/auth/{login,logout}`, `GET /me`,
      `GET /me/tenants`, `POST /api/v1/chat`, `POST /api/v1/chat/stream`
- ✅ Per-tenant Wazuh resolver + secrets backend (encrypted-file)
- ✅ Bootstrap CLI (`app.management.bootstrap_tenant`) and smoke-test CLI
      (`app.management.smoke_wazuh`)

### Gateway (`services/gateway/`)
- ⏳ Not started. Stub package only. Per the architecture, execute tools
      live here exclusively (Phase 6+ work — propose tools + approval gateway).

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
- Host: Linux laptop, GPU-equipped (migrated from CPU-only VM 2026-05-24)
- GPU: NVIDIA GeForce RTX 4050 Laptop (6 GB VRAM, driver 595.71.05, CUDA 13.2)
  — Profile B tight-end per `docs/13`. All four pre-pulled models confirmed
  100% GPU offload via `ollama ps`; qwen3:8b at 85% GPU / 15% CPU spillover
  (tight fit; see ADR 0010).
- OS: Ubuntu 24.04 (system Postgres 17 + pgvector via PostgreSQL APT repo)
- Python: 3.13.13 (pinned in `.python-version`, managed via `uv` 0.11.16)
- Node: 24.16.0 LTS, npm 11.13.0
- Ollama: 0.24.0 — pulled models: qwen3:4b, qwen3.5:4b, qwen3:8b, gemma3:4b, llama3.2:3b
- Wazuh: real deployment at `192.168.245.128` (Indexer :9200, Server API :55000,
  self-signed TLS; credentials in operator-supplied `credentials/` drop, gitignored)

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
- wolf-server: `7860` (running)
- wolf-dashboard: `3000` (running, Next.js 16 dev server)
- Ollama: `127.0.0.1:11434`
- Postgres: `127.0.0.1:5432` (system Postgres per ADR 0008)
- wolf-gateway: `8001` (not yet running)

**Wazuh tenant 'acme' on this machine** — bootstrapped 2026-05-24:
- Indexer: `https://192.168.245.128:9200`
- Server API: `https://192.168.245.128:55000`
- `verify_tls=False`, `inject_tenant_filter=False`
- Verified end-to-end: chat → guided strategy → `count_alerts_by_severity` tool
  → grounded answer ("325 alerts in 24h, 143 medium + 182 low") in 20.8s
  (vs ~76s cold on previous CPU-only VM — the GPU win materialized).

**Dev environment posture (per ADR 0008):** native is Wolf's primary
delivery channel; the dev environment uses system Postgres 17 +
pgvector (apt-installed, systemd-managed) to match the production
install path operators will use. Docker remains a supplementary
alternative for dev Postgres (documented in `ONBOARDING.md` §3.4)
and is the supplementary container-channel deployment for operators
who want to build their own images.

**CORS allow-origins:** `http://localhost:3000,http://127.0.0.1:3000,http://192.168.76.128:3000`

---

## 4. What's next

**Immediate next steps** (in priority order):
-1. ~~Multi-embedding RRF chaining (v1.5 + v2-moe via ADR 0014).~~
    **Shipped 2026-05-27.** Migration 0006 + secondary embedding
    column + 3-way RRF in `search()` + `--aux` mode on `wolf reembed`.
    Live-corpus benchmark: precision@5 35% → 60% on 20-query battery.
    Chained mode is `EMBEDDING_MODEL_AUX`-gated (empty default
    preserves Slice-2A behaviour). 99.5% of corpus (5145/5173)
    successfully embedded with v2-moe; remaining 28 chunks marked
    unembeddable but still retrievable via v1.5 + BM25 legs.
0. ~~Phase 3 follow-ups (judge model, agent_name lookup, reembed CLI,
   frontend integration).~~ **All four shipped 2026-05-27** in
   commit set following 05cb750. End-to-end verified with
   `GROUNDING_JUDGE_MODEL_ID=qwen3:8b` — judge caught a fabricated
   source-IP claim that qwen3:4b emitted confidently.
1. ~~Phase 3 Slice 3 — real seed corpora.~~ **Shipped 2026-05-27.**
   `tools/seed_knowledge` brings in 697 ATT&CK techniques + 4473
   Wazuh rules. End-to-end retest on the new dedicated agent at
   192.168.245.129 confirmed full pipeline: trigger brute force
   → Wazuh alerts → Wolf chat draws on both live alerts AND real
   ATT&CK/ruleset documentation.
2. **Stronger grounding judge** (now urgent with the rich corpus).
   qwen3:4b's judge JSON is unreliable at high evidence-prompt
   volumes — on the Slice 3 rich-corpus run the validator degraded
   gracefully (counts surfaced as None) because the judge returned
   malformed JSON. Options to evaluate: (a) route the validator to
   Nemotron 120B via the existing OpenRouter path (ADR 0005's
   hosted-API mechanism); (b) refine the judge prompt with explicit
   negative examples; (c) add a heuristic-overlap fallback that
   flags claims with low token overlap to citations. Worth an ADR
   now that real-corpus material exists to benchmark against.
3. **`search_alerts` agent-name lookup.** During the Slice 3 retest
   qwen3:4b passed `agent_id="linux-test-agent"` (the name) instead
   of `"001"` (the numeric ID) — Wazuh returned 0 hits. Adding an
   `agent_name` alias that resolves via a `list_agents` lookup
   eliminates this class of small-model confusion.
4. **`wolf reembed` helper** (queued from ADR 0012). Flipping
   `EMBEDDING_PROVIDER` without re-embedding silently degrades
   retrieval; the helper diffs `KnowledgeChunk.embedding_model`
   against the active provider and re-embeds the mismatches.
5. **Frontend integration of grounding verdict.** The chat response
   now carries `grounding_supported / unsupported / unverifiable`
   counts and the answer text contains `[unverified]` markers. The
   Next.js chat UI doesn't render these specially yet.
4. ~~Investigate Wazuh Server API 401 against `192.168.245.128`.~~
   **Resolved 2026-05-26.** Root cause: Wazuh Indexer and Server API
   maintain separate user databases; the operator's initial credential
   drop only provisioned the `wolf` user in the Indexer. Operator
   supplied the Server API admin (`wazuh-wui` / generated password).
   `bootstrap_tenant` re-run with per-endpoint credentials. End-to-end
   `/api/v1/chat` now verified with both pure-RAG (model picks
   `query_runbook`, retrieves ACME SOC runbook, cited answer in 60s)
   and mixed-mode (`get_rule_definition` + `query_runbook` in one
   loop, both citations attached). No Wolf code changes were needed.
5. **Pending workstation-class probe ADRs remain blocked on
   workstation GPU hardware (24+ GB VRAM):** GLM 5.1 ~32B (priority
   #1 per doc 15), Gemma 3 12B/27B, Qwen 3 14B/32B. Not blocking
   Phase 3 work.

**Phase 3 design touchpoints** (the order doc 06 implies):
- Vector store interface; pgvector implementation
- Ingestion pipeline (structure-aware chunking, metadata extraction)
- Seed corpora: Wazuh docs (via `tools/seed_knowledge`), ATT&CK
- Hybrid retrieval (vector + BM25)
- The `query_runbook` tool with metadata filters as first-class args
- The grounding validator: rejects ungrounded factual claims
- Per-tenant private corpus partition (storage-level isolation per
  doc 05's "RAG store" enforcement layer)

**Blocked / waiting:**
- Frontier-API verification needs an Anthropic or OpenAI key in the
  configured secrets backend (not blocking dev, only the formal exit check).

**Deferred** (deliberately not doing now):
- Phase 3 (RAG + grounding validator) — pending Phase 2 close-out.
  qwen3:4b's grounding-fabrication probe result makes Phase 3 *more*
  important if/when qwen becomes the default, not less.
- Phase 6 (gateway service + propose/execute tools) — structural, separate
  service; not until Phases 4 (multi-tenancy hardening) and 5 (cases) ship.
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

- **Cross-origin `NetworkError` after `wolf-cert init`** (2026-06-03).
  **RESOLVED 2026-06-03 in Slice 5.6-a** by introducing the
  wolf-dashboard reverse proxy: every `/api/v1/...` request now
  hits Next.js's catch-all route handler at
  `services/dashboard/app/api/[...path]/route.ts` and gets
  forwarded server-side to wolf-server. The browser only ever
  sees the dashboard origin, so there's no second origin's cert
  to trust. Phase 5.6-c will add mTLS between the proxy and
  wolf-server using the shared Wolf CA.
- **Conversations are in-memory only** (frontend `useState`).
  A page refresh wipes them. Full persistence plan captured in
  cross-session memory `conversation-tree-persistence-plan.md`
  for the eventual DB-storage phase: two-table schema
  (`conversations`, `message_nodes`), explicit `position` integer
  for stable sibling order, atomic version-add transaction
  (INSERT new node + UPDATE parent's `selected_child_id` in one
  tx), no path flattening on save, lossless round-trip test,
  tenant scoping via `TenantScopedQueryBuilder`. Land this when
  the project's general DB-storage phase begins; do not flatten
  to the active path on serialise — that would silently drop
  every off-branch subtree.
- **Planning bundle docs (`docs/00-vision-and-scope.md` →
  `docs/16-distribution-and-packaging.md`) still describe the
  pre-Phase-5.5 component names** (`services/orchestrator`,
  `frontend`, `app/`, etc.) throughout. Operationally inert —
  these are descriptive specs, not runtime configuration — but
  confusing for a new reader. Flagged for a dedicated doc-sweep
  slice after Phase 5.6 → 5.8 ship (likely alongside the
  installation-guide module). Found during the post-Phase-5.5
  exhaustive audit on 2026-06-03; deliberately deferred so the
  rename slice doesn't sprawl into a doc rewrite.
- **Inline security / efficiency gaps from Phase 5 prep.** The
  *quality-and-secure-coding-discipline* standing rule applies
  quality + secure coding inline at every slice but tracks
  deferred items (rate limits at the API boundary, additional
  audit-event categories for branch operations, secret-leakage
  scan of streaming text, etc.) for a dedicated post-feature
  hardening pass. Backlog accumulated through 5.0c — to be
  burned down in a focused slice labelled `5.0d` or similar
  before the open-source handover.
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

- **260 backend tests passing** (orchestrator-side, `services/
  orchestrator`). 0 failures, 0 skipped.
- **ruff:** clean across the workspace.
- **mypy strict: 0 errors** across orchestrator (66 source files),
  gateway (2), and all three workspace packages (`wolf_common`,
  `wolf_secrets`, `wolf_schema`). The Phase-0 PEP-561 blind spot
  that had hidden 56 errors since the very first phase commit was
  closed in `bf00c01` (2026-06-01). Workspace packages now ship
  `py.typed` markers; mypy resolves their imports correctly end-
  to-end.
- **Cross-tenant unit suite:** 8/8 passing
  (`services/orchestrator/tests/test_cross_tenant_isolation.py`,
  runs as part of the main suite).
- **Live tenant-isolation probe** (`tools/tenant_isolation_test`):
  6/6 checks pass against the dev two-tenant state. Run after every
  `services/` change per the *integrity-across-the-stack* standing
  rule.
- **Frontend:** `tsc --noEmit` clean, `eslint` clean. No frontend
  test framework wired yet — deferred to the dedicated hardening
  phase.
- **CI:** configured (`.github/workflows/ci.yml`); `origin/main` is
  current as of 2026-06-02 push.

---

## 8. Documentation status

- Planning bundle (`docs/00-13`): in git as of commit `c05cdce` (today).
- `docs/14-model-recommendations.md`: in git as of commit `b093761` (today).
- `docs/11-claude-code-instructions.md`: updated this session with the
  relaxed session-continuity protocol (reading required only for new env /
  new session / different model; end-of-session update remains mandatory).
  In git as of commit `b093761`.
- ADRs in `docs/decisions/`: 12 ADRs — 0001 (`llama3.2` baseline), 0002
  (`qwen3:4b`), 0003 (`gemma3:4b`), 0004 (default-model switch
  decision), 0005 (Phase 2 frontier-API exit-criterion verification),
  0006 (commitment to native support for four model families — Qwen 3,
  Llama 3, Gemma 3, GLM 5.1 ~32B), 0007 (native non-container
  delivery channel will be `.deb`/`.rpm` + systemd, fronted by a
  one-line install script — GitLab-style hybrid), 0008 (native
  delivery is primary; Docker is baseline-supported, not promoted;
  dev environment uses system Postgres), 0009 (qwen3.5:4b GPU probe —
  regression vs qwen3:4b on tool calling; supported but no default
  flip), 0010 (qwen3:8b GPU probe — same measured capability as
  qwen3:4b, tight VRAM fit with 85% GPU/15% CPU; KNOWN_MODELS
  amended), 0011 (opportunistic probe of IBM Granite 3.3 8B —
  outside the four-family commitment), 0012 (embedding stack —
  keep both Ollama and sentence-transformers adapters; Ollama
  default).  README index in place.
- `docs/15-supported-model-matrix.md`: directive document for the
  four-family commitment (added 2026-05-23 alongside ADR 0006).
- `docs/16-distribution-and-packaging.md`: living spec for the
  native-distribution channel committed to in ADR 0007 (added
  2026-05-23).  Implementation queued for post-Phase 4.
- `ONBOARDING.md` (repo root): single-entry onboarding doc — from
  `git clone` to first chat request — for a new contributor or a new
  Claude Code session on a different machine (added 2026-05-23).
- API docs: FastAPI auto-generates at `http://localhost:7860/docs`.
- README: in git as of commit `c05cdce`.

---

## 9. Hand-off note for next session

Phase 2 is functionally complete and closed at the exit-criteria
level (ADR 0005).  The default-model switch is done (`qwen3:4b`,
Apache 2.0, ADR 0004).  End-to-end re-verified on the user's real
Wazuh (192.168.76.129): qwen3:4b in `guided` mode, one tool call to
`count_alerts_by_severity`, grounded cited answer.  Multi-turn,
markdown, citations, tenant switcher all work in the Next.js 16
frontend at `http://192.168.76.128:3000`.

**This session (2026-05-23) added two product-direction artifacts and
one onboarding artifact:**

1. **ADR 0006 + `docs/15-supported-model-matrix.md`** — formal
   commitment to natively supporting four model families locally in
   dev: Qwen 3, Llama 3, Gemma 3, GLM 5.1 ~32B.  Production posture is
   user-choice (operators pick one or multiple, including hosted
   APIs).  Six-item "natively support" checklist defines the quality
   bar; four probe ADRs are now expected when workstation-GPU
   hardware lands.
2. **`ONBOARDING.md` at repo root** — single-entry onboarding doc
   covering: 60-second orientation, mandatory reading order, system
   requirements, first-time setup from a clean clone (12 steps),
   verification (tests / lint / smoke / probe), operational tasks,
   seven real gotchas with fixes, session-continuity protocol, file
   reference table, troubleshooting matrix.  Written specifically to
   make a different-machine resume seamless.

**Single most important thing for the next session to know:** the
project owner is arranging a GPU dev machine.  When you (Claude Code
on the new machine) resume, **read `ONBOARDING.md` first**, then
`docs/PROGRESS.md` (this file), then `docs/CHANGELOG.md` recent
entries, then ADRs 0001–0006.  The next concrete work is either (a)
the four pending probe ADRs once Ollama is set up on the GPU machine
with the larger models pulled, or (b) Phase 3 design and the
grounding validator — both can be done in parallel.

Operator notes (unchanged from 2026-05-22 session):
- OpenRouter API key is stashed in `.local/secrets.enc` under
  `model.openrouter.api_key`.  Operator pasted it once for the ADR
  0005 verification; it should be rotated via openrouter.ai/keys.
  **NB:** `.local/` is gitignored — the encrypted secrets blob and
  Fernet key live only on the current dev VM.  A new dev machine
  starts from a fresh `.env` and an empty secrets backend (see
  `ONBOARDING.md` §3.5 and §3.10).
- To re-run the frontier verification any time, flip three env vars
  (DEFAULT_MODEL_PROVIDER=openai, DEFAULT_MODEL_ID=nvidia/nemotron-3-
  super-120b-a12b:free, OPENAI_BASE_URL=https://openrouter.ai/api),
  restart orchestrator, run the chat.  No key re-share needed.
- Run `uv run python -m app.management.smoke_wazuh --tenant-slug acme
  --all-tools` any time you want to re-verify every read tool against
  the live deployment (e.g. after a Wazuh upgrade).

Operational note: services run as `nohup` background processes (not
systemd / compose).  On host reboot you must restart Ollama, the
orchestrator, and the frontend by hand.  Orchestrator needs the env
vars in Section 3; the canonical bundle lives at `/tmp/orchestrator.env`.
