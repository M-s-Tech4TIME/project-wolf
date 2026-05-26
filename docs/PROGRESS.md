# Wolf — Development Progress

> **This is the live state of the Wolf project.** Read this file at the start
> of every Claude Code session, before doing anything else. Update it at the
> end of every session.
>
> For history of what changed when, see `CHANGELOG.md` (append-only).

**Last updated:** 2026-05-27 by claude-code

---

## 1. Where we are right now

**Current phase:** Phase 3 — Knowledge & RAG (per `docs/10-build-roadmap.md`).

**Phase status:** **Phase 3 Slices 1, 1.5, and 2 (A+B) shipped**.
Phase 2 closed (ADR 0005). Slice 1: vertical RAG operational
end-to-end. Slice 1.5: sentence-transformers second adapter behind
optional dep + decision ADR 0012. Slice 2A (hybrid retrieval):
migration 0005 added a `content_tsv` generated column + GIN index,
`PgvectorKnowledgeStore.search()` now fuses vector (cosine) and FTS
(`ts_rank_cd`) candidates via Reciprocal Rank Fusion (k=60). Slice 2B
(grounding validator): `app/grounding/` module with LLM-as-judge
that flags unsupported factual claims `[unverified]` inline,
hooked into `AgentLoop.run()` before the answer event, surfaced as
`grounding_supported / unsupported / unverifiable` on the chat
response. `make check` 162 passed (128 prior + 19 Slice-1/1.5
knowledge tests + 16 Slice-2 validator tests). End-to-end on the
real Wazuh confirms pipeline works; judge-model strength on hard
cases is a documented limitation (qwen3:4b plays safe with
"unverifiable" labels rather than catching subtle embellishment —
follow-up queued).

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
- Orchestrator: `8000` (running)
- Frontend: `3000` (running, Next.js 16 dev server)
- Ollama: `127.0.0.1:11434`
- Postgres: `127.0.0.1:5432` (system Postgres per ADR 0008)
- Gateway: `8001` (not yet running)

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
1. **Phase 3 Slice 3 — real seed corpora.** `tools/seed_knowledge`
   scrapers for Wazuh docs + ATT&CK enterprise-attack.json. Replaces
   the 9-chunk dev inline seed; gives Slice 1.5's precision benchmark
   real material to evaluate against and gives Slice 2B's grounding
   validator more diverse evidence to judge on.
2. **Phase 3 Slice 2 follow-up — stronger grounding judge.** Slice 2B
   shipped the architecture; qwen3:4b as the judge under-flags subtle
   embellishments (defaults to "unverifiable" on hard cases). Options
   to evaluate: (a) route the validator to Nemotron 120B via the
   existing OpenRouter path (ADR 0005's hosted-API mechanism) for
   stronger judging; (b) refine the judge prompt with explicit
   negative examples; (c) add a heuristic-overlap fallback that flags
   claims with low token overlap to citations. Worth an ADR-level
   evaluation once Slice 3's real corpus produces enough verdict
   samples to measure precision/recall on.
3. **`wolf reembed` helper** (queued from ADR 0012). Currently
   flipping `EMBEDDING_PROVIDER` without re-embedding silently
   degrades retrieval; the helper diffs `KnowledgeChunk.embedding_model`
   against the active provider and re-embeds the mismatches.
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

- **162 backend tests passing** (128 prior + 19 knowledge-layer tests
  across Slices 1/1.5/2A + 16 grounding-validator tests in Slice 2B)
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
- API docs: FastAPI auto-generates at `http://localhost:8000/docs`.
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
