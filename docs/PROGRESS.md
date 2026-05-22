# Wolf — Development Progress

> **This is the live state of the Wolf project.** Read this file at the start
> of every Claude Code session, before doing anything else. Update it at the
> end of every session.
>
> For history of what changed when, see `CHANGELOG.md` (append-only).

**Last updated:** [DATE] by [SESSION: human/claude-code]

---

## 1. Where we are right now

**Current phase:** [e.g., "Phase 2 — Read path, end to end" from `docs/10-build-roadmap.md`]

**Phase status:** [e.g., "5 of 9 read tools wired to real Wazuh; 4 mock-only; orchestrator agent loop working; capability probe built but not yet run against live Ollama"]

**Phase exit criteria progress:**
- [ ] [Criterion 1 from roadmap]
- [x] [Criterion that's done]
- [ ] [Criterion in progress]

---

## 2. What's currently built and working

Group by service/layer. Mark each item with status: ✅ working, 🟡 partial, ❌ broken/disabled.

### Orchestrator (`services/orchestrator/`)
- ✅ FastAPI app skeleton
- ✅ Model abstraction layer (`app/models/`): Anthropic, OpenAI, Ollama adapters
- ✅ Capability descriptor + KNOWN_MODELS registry
- ✅ Tool registry (`app/tools/registration.py`)
- 🟡 Read tools — 5/9 verified against real Wazuh, 4/9 mock-only
- ❌ [anything intentionally disabled]

### Gateway (`services/gateway/`)
- [status]

### Frontend (`services/frontend/`)
- [status]

### Shared packages (`packages/`)
- ✅ `schema/wolf_schema/` — canonical types

### Tooling (`tools/`)
- ✅ `model_probe/` — built, **not yet run** against live Ollama
- ✅ `tenant_isolation_test/` — [status]
- ✅ `seed_knowledge/` — [status]

### Infrastructure
- ✅ Docker Engine on dev VM (192.168.76.128)
- ✅ Postgres + pgvector container
- ✅ Ollama at localhost:11434 running llama3.2:latest
- ✅ Wazuh at 192.168.76.129 (sibling VM, VMware NAT)
- ❌ [Keycloak / OpenBao / etc. — note if not yet up]

---

## 3. Current configuration

The minimum a new session needs to know to be productive immediately.

**Dev environment:**
- Host: VM, 16 GB RAM, CPU-only (no GPU)
- OS: [Ubuntu 24.04 / etc.]
- Python: 3.13.x (`uv` managed)
- Node: 24 LTS

**Model defaults:**
- `DEFAULT_MODEL_PROVIDER`: `ollama`
- `DEFAULT_MODEL_ID`: `llama3.2`
- Adapters active: Anthropic, OpenAI, Ollama

**Wazuh connection:**
- Indexer: `https://192.168.76.129:9200` (or actual)
- Server API: `https://192.168.76.129:55000` (or actual)
- Credentials: in [secrets backend location]

**Service ports (dev):**
- Orchestrator: `8000`
- Gateway: `8001`
- Frontend: `3000`
- Postgres: `5432`
- Keycloak: `8080`
- Ollama: `11434`

---

## 4. What's next

**Immediate next steps** (in priority order):
1. [Next concrete task — e.g., "Run capability probe against llama3.2 on this hardware"]
2. [Second task]
3. [Third task]

**Blocked / waiting:**
- [Anything blocked, and on what]

**Deferred** (deliberately not doing now):
- [Things we explicitly chose not to do yet, with one-line reason]

---

## 5. Active decisions and open questions

Things that need a human call before they can proceed. Move resolved items to
`CHANGELOG.md` as ADRs.

- [ ] [e.g., "Switch DEFAULT_MODEL_ID from llama3.2 to qwen3:4b after probe — pending probe results"]
- [ ] [e.g., "Choose between Qdrant and pgvector for production — currently using pgvector by default"]

---

## 6. Known issues and tech debt

Things that work but should be fixed. Not blockers.

- [Description of issue — file/line if applicable — severity]

---

## 7. Test coverage status

- Unit tests: [pass count] passing, [fail/skip count] failing/skipped
- Integration tests: [status]
- Cross-tenant isolation suite: [status, when last run, against which tenants]
- CI: [last green run, against which commit]

---

## 8. Documentation status

- Planning bundle (`docs/00-13`): [in git? last reviewed?]
- ADRs in `docs/decisions/`: [count, last added]
- API docs: [auto-generated? location?]
- README: [last reviewed]

---

## 9. Hand-off note for next session

A short paragraph in plain English: if a brand-new Claude Code session opens
this file tomorrow, on this machine or another, what is the single most
important thing it needs to know to be useful?

> [Free-form note. Updated at end of every session.]
