# Wolf — Onboarding (Start Here)

> **You are a new contributor (human or AI) who has just cloned this
> repo.** This file gets you from `git clone` to a working dev
> environment with a chat session against a real Wazuh, then orients
> you for whatever phase of work is next.
>
> **Read this file end-to-end before doing anything else.** Then read
> the documents it points you at, in the order it specifies.

**Last verified:** 2026-05-23 against commit on `main` at the time this
file was written. The repo is moving; if commands here drift from
reality, trust the code, then fix this file in your first commit.

---

## 0. Sixty-second orientation

**Wolf** is an open-source, model-agnostic, agentic AI platform that
sits *beside* a Wazuh deployment (Indexer + Server API) and helps
analysts, detection engineers, and MSSPs operate it. It reads freely,
proposes state-changing actions, and never executes them without an
authenticated human approval. The full pitch is in
[`README.md`](README.md) and [`docs/00-vision-and-scope.md`](docs/00-vision-and-scope.md).

The codebase is divided into three deployable services plus shared
packages and tooling:

```
project-wolf/
├── docs/                  # 16 numbered planning docs + decisions/ (ADRs) + PROGRESS.md + CHANGELOG.md
├── packages/              # Shared Python libraries (common, secrets, schema)
├── services/
│   ├── orchestrator/      # FastAPI service — the brain (agent loop, tools, auth, audit)
│   └── gateway/           # FastAPI service — Phase 4+ propose/execute path (stub today)
├── frontend/              # Next.js 16 app (login, chat, citations, tenant switcher)
├── tools/                 # CLIs: model_probe, seed_knowledge, tenant_isolation_test
├── deploy/                # Dockerfiles, Compose, k8s manifests
└── .github/workflows/     # CI (lint / typecheck / test / safety / local-model-check)
```

**Where you are in the build** lives in
[`docs/PROGRESS.md`](docs/PROGRESS.md) — read it second, after this
file. **What changed when** lives in
[`docs/CHANGELOG.md`](docs/CHANGELOG.md) (append-only).

---

## 1. Mandatory reading order

Do these in order. The numbered docs build on each other; skipping
them costs you more time than reading them.

### Tier 1 — Read fully before writing any code (60–90 min)

1. [`docs/PROGRESS.md`](docs/PROGRESS.md) — live state. Tells you what
   exists, what's broken, what's next.
2. [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — last 5–10 entries. Tells
   you what shipped recently and why.
3. [`docs/00-vision-and-scope.md`](docs/00-vision-and-scope.md) — the
   core principles. These constrain every decision you make.
4. [`docs/01-architecture.md`](docs/01-architecture.md) — components,
   data flow, trust tiers.
5. [`docs/11-claude-code-instructions.md`](docs/11-claude-code-instructions.md)
   — direct working rules for an AI coding agent, including the
   relaxed session-continuity protocol. Humans should still skim it.
6. [`docs/decisions/README.md`](docs/decisions/README.md) — index of
   ADRs. Then read every ADR that's marked `accepted` (currently
   0001–0006). They explain *why* things are the way they are.

### Tier 2 — Read before working in that area

- Touching the agent loop or models? → [`docs/02-model-abstraction.md`](docs/02-model-abstraction.md), [`docs/14-model-recommendations.md`](docs/14-model-recommendations.md), [`docs/15-supported-model-matrix.md`](docs/15-supported-model-matrix.md).
- Touching tools? → [`docs/03-tool-catalog-and-capability-tiers.md`](docs/03-tool-catalog-and-capability-tiers.md).
- Touching tenancy or auth? → [`docs/05-multi-tenancy.md`](docs/05-multi-tenancy.md), [`docs/07-security-and-threat-model.md`](docs/07-security-and-threat-model.md).
- Starting Phase 3 (RAG)? → [`docs/06-knowledge-and-rag.md`](docs/06-knowledge-and-rag.md), [`docs/10-build-roadmap.md`](docs/10-build-roadmap.md) §"Phase 3".
- Setting up new hardware? → [`docs/13-system-requirements.md`](docs/13-system-requirements.md).
- Vocabulary check? → [`docs/12-glossary.md`](docs/12-glossary.md).

### Tier 3 — Reference

- [`docs/04-approval-gateway.md`](docs/04-approval-gateway.md) — Phase 4+.
- [`docs/08-reporting-and-orchestration.md`](docs/08-reporting-and-orchestration.md) — Phase 5+.
- [`docs/09-tech-stack-and-repo-layout.md`](docs/09-tech-stack-and-repo-layout.md) — the original layout proposal (some drift; reality is what's described in this file).

---

## 2. System requirements

### Mandatory

- **OS:** Linux (Ubuntu 24.04 LTS verified). macOS likely works but is unverified.
- **Python 3.13** — pinned in [`.python-version`](.python-version), managed by `uv`.
- **Node.js 24 LTS** — pinned in [`.nvmrc`](.nvmrc). Any 24.x works.
- **`uv`** — Python project / dependency manager. Install: `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- **`npm`** (ships with Node 24) — used to install the frontend's dependencies in [`frontend/`](frontend/).
- **Docker + Docker Compose v2** — only for Postgres in dev. Services run directly on the host.
- **Ollama** — local model runtime. Install: `curl -fsSL https://ollama.com/install.sh | sh`. https://ollama.com.

### Optional

- **A GPU** — drastically improves model latency. The four-family matrix in [`docs/15-supported-model-matrix.md`](docs/15-supported-model-matrix.md) expects workstation-GPU hardware (24+ GB VRAM) to be fully exercised. CPU-only is the floor, not the ceiling.
- **A reachable Wazuh deployment** — Indexer (default :9200) and Server API (default :55000). Required for live-data smoke tests, not for unit tests.

### Network ports used by the dev stack

| Port | Service | Bound | Notes |
|---|---|---|---|
| 8000 | Orchestrator (FastAPI) | 0.0.0.0 | LAN-reachable for browser access |
| 8001 | Gateway (FastAPI) | 0.0.0.0 | Stub today; will be needed Phase 4+ |
| 3000 | Frontend (Next.js dev) | 0.0.0.0 | LAN-reachable |
| 5432 | Postgres | 0.0.0.0 | docker compose default |
| 11434 | Ollama | 127.0.0.1 | Local only by default |

---

## 3. First-time setup from a clean clone

This is the full path from `git clone` to first request answered. Do
not skip steps; each one is small.

### 3.1 Clone and enter

```bash
git clone git@github.com:M-s-Tech4TIME/project-wolf.git
cd project-wolf
```

### 3.2 Install Python deps

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Sync all workspace packages (orchestrator, gateway, packages/*)
uv sync --all-packages
```

This creates `.venv/` at the repo root and installs everything in
editable mode.

### 3.3 Install frontend deps

```bash
cd frontend
npm install
cd ..
```

### 3.4 Start Postgres

```bash
docker compose up -d postgres
```

This is the only container the dev workflow uses by default. The
orchestrator and frontend run directly on the host so hot-reload works
without rebuilding images.

### 3.5 Generate dev secrets

The orchestrator needs two secrets in `.env`:

```bash
# SECRET_KEY — used for JWT signing. Must be >= 32 chars.
python -c 'import secrets; print(secrets.token_urlsafe(48))'

# SECRETS_FILE_KEY — Fernet key for the encrypted-file secrets backend.
uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

### 3.6 Write `.env`

Copy [`.env.example`](.env.example) to `.env` and fill in the two
secrets above:

```bash
cp .env.example .env
$EDITOR .env
```

Defaults from `.env.example` are fine for everything else *if* you are
using Postgres + Ollama with the steady-state default model (`qwen3:4b`).

### 3.7 Run database migrations

```bash
# Inside services/orchestrator so alembic finds its config.
cd services/orchestrator
uv run alembic upgrade head
cd ../..
```

You should see migrations `0001_initial_schema`, `0002_tenant_wazuh_config`, `0003_inject_tenant_filter` apply cleanly.

### 3.8 Install Ollama + pull the default model

```bash
# Install Ollama (skip if already installed)
curl -fsSL https://ollama.com/install.sh | sh

# Start the daemon (if not already)
ollama serve &     # or use systemctl on systems that have a service unit

# Pull the project's steady-state default (Apache 2.0, see ADR 0004)
ollama pull qwen3:4b
```

For the broader supported-model matrix, see
[`docs/15-supported-model-matrix.md`](docs/15-supported-model-matrix.md).
On a GPU-equipped machine, also pull the larger sizes you intend to
exercise (e.g. `ollama pull qwen3:32b`, `ollama pull glm-5.1`).

### 3.9 Bootstrap a tenant and an admin user

```bash
cd services/orchestrator
uv run python -m app.management.bootstrap_tenant \
    --tenant-slug acme \
    --tenant-name "Acme SecOps" \
    --user-email admin@example.com \
    --user-password 'choose-a-strong-password'
cd ../..
```

This creates one tenant (`acme`), one admin user, and writes the
initial state needed for login. If you already have a database with
tenants, skip this step.

### 3.10 (Optional) Wire a real Wazuh

If you have a Wazuh deployment to point Wolf at:

```bash
cd services/orchestrator

# Stash the Wazuh credentials in the encrypted secrets backend.
# These commands read the value from stdin so the secret never touches
# shell history or argv.
printf 'YOUR_INDEXER_PASSWORD' | uv run python -m app.management.set_secret \
    --key tenant.acme.wazuh.indexer.password
printf 'YOUR_API_PASSWORD' | uv run python -m app.management.set_secret \
    --key tenant.acme.wazuh.api.password

# Wire the URLs and usernames to the tenant.
# (See app/management/bootstrap_tenant.py for the full flag set, including
# --wazuh-indexer-url, --wazuh-api-url, --wazuh-indexer-user, --wazuh-api-user,
# and --wazuh-verify-tls. Re-run bootstrap_tenant with these flags or use the
# tenancy admin endpoints once they exist.)

cd ../..
```

Without this step, the read tools will return zero results (no Wazuh
configured for the tenant) — auth and the agent loop still work.

### 3.11 Start the services

In two separate terminals (or use `nohup` / `tmux`):

```bash
# Terminal 1 — orchestrator
cd services/orchestrator
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — frontend
cd frontend
npm run dev -- --hostname 0.0.0.0 --port 3000
```

**Important:** always `cd services/orchestrator` before launching
uvicorn. See Gotcha #1 — running it from repo root picks up the
gateway's `app/` package instead of orchestrator's.

### 3.12 First request

```bash
# Health check
curl -fsS http://localhost:8000/healthz

# Login (saves cookie to /tmp/wolf-cookie.txt)
curl -fsS -c /tmp/wolf-cookie.txt -H 'Content-Type: application/json' \
    -d '{"email":"admin@example.com","password":"choose-a-strong-password"}' \
    http://localhost:8000/api/v1/auth/login

# Send a chat question
curl -fsS -b /tmp/wolf-cookie.txt -H 'Content-Type: application/json' \
    -d '{"question":"how many alerts in the last 24 hours by severity?"}' \
    http://localhost:8000/api/v1/chat
```

Or open the frontend at `http://localhost:3000` (or your LAN IP, e.g.
`http://192.168.1.50:3000`), log in, and chat from the UI.

---

## 4. Verifying everything works

Run these in order. If any fails, fix it before moving on.

### 4.1 Unit + integration tests (128 currently passing)

```bash
make test                # full backend suite
make test-isolation      # the cross-tenant isolation suite alone
make test-cov            # with coverage report; gates at 80%
```

### 4.2 Lint + typecheck

```bash
make lint                # ruff
make typecheck           # mypy strict on safety-critical packages
make check               # lint + typecheck + test
```

### 4.3 Live smoke against your real Wazuh (only if you wired one in 3.10)

```bash
cd services/orchestrator
uv run python -m app.management.smoke_wazuh --tenant-slug acme --all-tools
```

This exercises every registered read tool against the live deployment.
It is the canonical "does Wolf actually talk to Wazuh" check and the
one you re-run after any Wazuh upgrade or tool change.

### 4.4 Frontend build

```bash
cd frontend
npm run build      # production build
npm run lint       # eslint
cd ..
```

### 4.5 Model capability probe (optional — needed when adding a model)

```bash
# From repo root
uv run python -m tools.model_probe --provider ollama --model qwen3:4b
uv run python -m tools.model_probe --provider ollama --model llama3.2
# (etc.)
```

Capture probe results as an ADR — see ADR 0001/0002/0003 for the pattern
and [`docs/14-model-recommendations.md`](docs/14-model-recommendations.md)
§"Environment-change playbook" for the full mechanical procedure.

---

## 5. Common operational tasks

### Restart the stack after a reboot

```bash
# 1. Postgres (if Docker isn't set to auto-start)
docker compose up -d postgres

# 2. Ollama
ollama serve &

# 3. Orchestrator
cd services/orchestrator
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
cd ../..

# 4. Frontend
cd frontend
npm run dev -- --hostname 0.0.0.0 --port 3000 &
cd ..
```

### Add a new tenant

```bash
cd services/orchestrator
uv run python -m app.management.bootstrap_tenant \
    --tenant-slug <slug> --tenant-name "<Display Name>" \
    --user-email <email> --user-password <password>
cd ../..
```

### Wire a different Wazuh to an existing tenant

Stash the credentials with `set_secret` (Section 3.10 pattern), then
update the tenant's `TenantWazuhConfig` row. Today this is done via
re-running `bootstrap_tenant` with the wazuh flags, or directly in the
DB. A dedicated CLI / admin endpoint is unwritten.

### Flip the default model

1. Pull the candidate model with Ollama.
2. Run `tools.model_probe` against it.
3. Write an ADR (`docs/decisions/0NNN-...md`) following the ADR 0004 pattern.
4. Change `default_model_id` in [`services/orchestrator/app/config.py`](services/orchestrator/app/config.py).
5. Restart orchestrator. Verify with a chat curl.

Full procedure in [`docs/14-model-recommendations.md`](docs/14-model-recommendations.md) §"Environment-change playbook".

### Use a hosted API instead of Ollama

```bash
# Stash the key once, never share it again
printf 'sk-...' | uv run python -m app.management.set_secret \
    --key model.openrouter.api_key

# Override the model envs (OpenAI-compatible adapter)
export DEFAULT_MODEL_PROVIDER=openai
export DEFAULT_MODEL_ID=nvidia/nemotron-3-super-120b-a12b:free
export OPENAI_BASE_URL=https://openrouter.ai/api    # NOT .../api/v1 — see Gotcha #2
export DEFAULT_MODEL_API_KEY_REF=model.openrouter.api_key

# Restart orchestrator with this env
```

The full verification pattern is documented in ADR 0005.

### Rotate a secret

`set_secret` overwrites in place. Pipe the new value to it the same way you piped the original.

### Run a one-off Alembic migration

```bash
cd services/orchestrator
uv run alembic revision --autogenerate -m "add column foo to tenants"
$EDITOR migrations/versions/<new_file>.py     # review the autogen output
uv run alembic upgrade head
cd ../..
```

---

## 6. Gotchas (real ones that bit us)

### Gotcha #1 — Two `app/` packages collide

Both `services/orchestrator/app/` and `services/gateway/app/` expose
a top-level Python package named `app`. With the editable workspace
install, whichever one Python finds first on `sys.path` wins.

- **For uvicorn:** always `cd services/orchestrator` before
  `uvicorn app.main:app ...`. Running from repo root picks the
  gateway's `app/`, which has `/healthz` but none of the chat/auth
  routes, and `/api/v1/auth/login` returns 404.
- **For the model_probe CLI:** the CLI's `__main__.py` already has a
  `sys.path` bootstrap to force orchestrator's `app/` to the front.
  Don't remove it.
- **The deeper fix** (rename one of the packages) is deferred. ADR
  0005 §"Three real issues" documents this as recurring tech debt.

### Gotcha #2 — `OPENAI_BASE_URL` must NOT include `/v1`

The OpenAI adapter appends `/v1/chat/completions` itself. Setting
`OPENAI_BASE_URL=https://openrouter.ai/api/v1` produces a doubled `/v1`
and a 404. Correct: `https://openrouter.ai/api`. Documented inline on
the OpenRouter `KNOWN_MODELS` entries in
[`services/orchestrator/app/models/interface.py`](services/orchestrator/app/models/interface.py).

### Gotcha #3 — `inject_tenant_filter` is opt-in for a reason

A stock Wazuh deployment does not stamp `tenant_id` on documents.
If you set `TenantWazuhConfig.inject_tenant_filter=True` against a
vanilla Wazuh, every read tool returns zero results — Wolf is
filtering correctly, the data just doesn't carry the field. Leave it
`False` (the default) for single-tenant / standalone deployments;
turn it on for MSSP deployments where ingestion stamps the field at
indexing time. See [`docs/05-multi-tenancy.md`](docs/05-multi-tenancy.md).

### Gotcha #4 — LAN access needs three settings

If you want to reach the orchestrator + frontend from a different
machine on the LAN (e.g. a browser on your laptop hitting the VM's
IP), check all three:

1. **Orchestrator bound `0.0.0.0`**, not `127.0.0.1` (the `--host 0.0.0.0` flag in Section 3.11).
2. **`CORS_ALLOW_ORIGINS`** in `.env` includes the LAN-IP origin (e.g. `http://192.168.1.50:3000`).
3. **`allowedDevOrigins`** in [`frontend/next.config.ts`](frontend/next.config.ts) includes the LAN IP (Next 16 enforces this for cross-origin dev requests).

### Gotcha #5 — Models occasionally send `{"limit": null}`

Small models sometimes emit explicit-null fields for optional
parameters. The dispatcher strips them
([`services/orchestrator/app/tools/dispatcher.py`](services/orchestrator/app/tools/dispatcher.py),
`strip_explicit_nulls`). If you add a new tool with optional fields,
this protection is already in place — don't disable it.

### Gotcha #6 — Relative-time strings on alert tools

Some models pass `time_from="now-24h"` instead of an ISO timestamp.
[`services/orchestrator/app/tools/alerts.py`](services/orchestrator/app/tools/alerts.py)
has a Pydantic `field_validator` to parse this. If you add a tool that
accepts time inputs, copy the validator pattern.

---

## 7. The session-continuity protocol

Wolf has a small protocol so any Claude Code session (or human) can
resume work cleanly without re-deriving context from git log.

- **[`docs/PROGRESS.md`](docs/PROGRESS.md)** — live snapshot of where
  the project is *now*. Updated at the end of every session that
  changed state. Read it first on a new session.
- **[`docs/CHANGELOG.md`](docs/CHANGELOG.md)** — append-only history.
  One entry per session, even "investigation only" sessions. Newest
  on top. Be specific (the file's own header explains why).
- **[`docs/decisions/`](docs/decisions/)** — ADRs. One file per
  decision, numbered, never rewritten. See
  [`docs/decisions/README.md`](docs/decisions/README.md) for format.
- **AI memory** (Claude Code only) —
  `~/.claude/projects/<encoded-cwd>/memory/MEMORY.md` plus per-topic
  files. Auto-loaded by the agent on every turn. Not in the repo.

The full protocol — including the relaxed reading requirement and the
mandatory end-of-session update + commit — is in
[`docs/11-claude-code-instructions.md`](docs/11-claude-code-instructions.md).
Read it. It's short.

---

## 8. The current state in one paragraph

(Always cross-check this against [`docs/PROGRESS.md`](docs/PROGRESS.md)
— that file is the source of truth.)

As of 2026-05-23: **Phase 2 (read path, end-to-end) is closed at the
exit-criteria level** (ADR 0005). The agent loop works against a real
Wazuh in three strategies (frontier / guided / pipeline) on both a
local Ollama model (`qwen3:4b`, the steady-state default per ADR 0004)
and a hosted frontier-tier model (`nvidia/nemotron-3-super-120b-a12b:free`
via OpenRouter). 9 of 9 read tools verified live. 128 backend tests
passing. mypy strict clean on 33 safety-critical files. Frontend
(Next.js 16) renders chat, citations, multi-turn, tenant switcher.

**Next phase: Phase 3** — RAG + grounding validator per
[`docs/06-knowledge-and-rag.md`](docs/06-knowledge-and-rag.md) and
[`docs/10-build-roadmap.md`](docs/10-build-roadmap.md). The grounding
validator is the designed solution for the `qwen3:4b`
grounding-discipline probe failure recorded in ADR 0002.

**Open commitment that may need new hardware:** ADR 0006 commits Wolf
to natively supporting four model families locally (Qwen 3, Llama 3,
Gemma 3, GLM 5.1 ~32B). Four probe ADRs (GLM 5.1, Gemma 12B/27B, Qwen
14B/32B, larger Llama) are expected once workstation-GPU hardware is
available. See [`docs/15-supported-model-matrix.md`](docs/15-supported-model-matrix.md).

---

## 9. Quick file-location reference

| What | Where |
|---|---|
| App entrypoint (FastAPI) | [`services/orchestrator/app/main.py`](services/orchestrator/app/main.py) |
| Config / env settings | [`services/orchestrator/app/config.py`](services/orchestrator/app/config.py) |
| Agent loop (strategies) | [`services/orchestrator/app/agent/`](services/orchestrator/app/agent/) |
| Model adapters + KNOWN_MODELS | [`services/orchestrator/app/models/`](services/orchestrator/app/models/) |
| Tool definitions + dispatcher | [`services/orchestrator/app/tools/`](services/orchestrator/app/tools/) |
| Wazuh clients (Indexer + API) | [`services/orchestrator/app/wazuh/`](services/orchestrator/app/wazuh/) |
| Tenancy + auth | [`services/orchestrator/app/tenancy/`](services/orchestrator/app/tenancy/), [`services/orchestrator/app/auth/`](services/orchestrator/app/auth/) |
| Audit log | [`services/orchestrator/app/audit/`](services/orchestrator/app/audit/) |
| Guardrails | [`services/orchestrator/app/guardrails/`](services/orchestrator/app/guardrails/) |
| Management CLIs | [`services/orchestrator/app/management/`](services/orchestrator/app/management/) |
| Alembic migrations | [`services/orchestrator/migrations/versions/`](services/orchestrator/migrations/versions/) |
| Backend tests | [`services/orchestrator/tests/`](services/orchestrator/tests/) |
| Shared schema types | [`packages/schema/wolf_schema/`](packages/schema/wolf_schema/) |
| Secrets backend | [`packages/secrets/wolf_secrets/`](packages/secrets/wolf_secrets/) |
| Logging / tracing helpers | [`packages/common/wolf_common/`](packages/common/wolf_common/) |
| Frontend app | [`frontend/`](frontend/) |
| Frontend chat shell | [`frontend/components/chat-shell.tsx`](frontend/components/chat-shell.tsx) |
| Frontend SSE hook | [`frontend/hooks/use-chat-stream.ts`](frontend/hooks/use-chat-stream.ts) |
| Frontend Next config (CORS / origins) | [`frontend/next.config.ts`](frontend/next.config.ts) |
| Capability probe CLI | [`tools/model_probe/`](tools/model_probe/) |
| Compose (Postgres in dev) | [`docker-compose.yml`](docker-compose.yml), [`docker-compose.dev.yml`](docker-compose.dev.yml) |
| Makefile (test / lint / typecheck / probe targets) | [`Makefile`](Makefile) |
| CI | [`.github/workflows/ci.yml`](.github/workflows/ci.yml) |

---

## 10. When something doesn't work

In rough order of "what to try first":

| Symptom | Likely cause | Where to look |
|---|---|---|
| `/api/v1/auth/login` returns 404 | uvicorn picked up gateway's `app/` | Gotcha #1; `cd services/orchestrator` first |
| Chat returns "no tools called" or empty answer | Model entry in `KNOWN_MODELS` says `recommended_strategy='pipeline'` for a model that can actually do native tool calls | Re-probe; amend entry; see commit `14cc727` for the pattern |
| Read tools return 0 results | `inject_tenant_filter=True` on a vanilla Wazuh | Gotcha #3; flip to False |
| Hosted API returns 404 | `OPENAI_BASE_URL` includes `/v1` | Gotcha #2 |
| LAN browser can't load the frontend | One of three things misconfigured | Gotcha #4 |
| `loop_error` mid-conversation | Model adapter raised; check the audit table for the captured exception type + traceback (commit `e09b4e5`) | `services/orchestrator/app/agent/loop.py` |
| Tests fail on a clean checkout | First check Postgres is up and migrations are applied | `make test` after `docker compose up -d postgres` + `make migrate-local` |
| mypy complains about a new file | The strict gate covers the safety-critical packages listed in the [`Makefile`](Makefile) `typecheck` target | Add explicit types or move it outside the gated set with justification |

If none of the above match, the audit log table (`audit_log` in
Postgres) records every model call and tool call with arguments and
results. Read the last few rows for the failing tenant — the answer
is almost always in there.

---

## 11. What to do right after onboarding

1. Confirm `make check` passes on your machine.
2. Confirm `smoke_wazuh --all-tools` passes (if you have a real Wazuh).
3. Pick up whatever [`docs/PROGRESS.md`](docs/PROGRESS.md) §4 ("What's
   next") names as the next work item. As of today that's Phase 3
   (RAG + grounding validator), with the four supported-family probes
   blocked on GPU hardware (per ADR 0006).
4. At end of session: update [`docs/PROGRESS.md`](docs/PROGRESS.md),
   append an entry to [`docs/CHANGELOG.md`](docs/CHANGELOG.md), and
   commit. See [`docs/11-claude-code-instructions.md`](docs/11-claude-code-instructions.md).

Welcome to Wolf.
