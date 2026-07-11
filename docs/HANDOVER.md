# Wolf — Project Handover (development paused 2026-07-12)

> **Read this first when resuming the project after the break.**
> This document is the wrap-up snapshot taken when active development was
> paused on 2026-07-12. It stitches together everything a returning
> developer (human or Claude Code) needs: what Wolf is, exactly where the
> build stands, what comes next, every standing rule in force, how to
> rebuild the development environment, and where the credentials go.
>
> Companion documents:
> - **[CLAUDE-RESUME-PROMPT.md](CLAUDE-RESUME-PROMPT.md)** — a ready-to-paste
>   prompt that boots a fresh Claude Code session straight into productive work.
> - **[../ONBOARDING.md](../ONBOARDING.md)** — the canonical, step-by-step
>   development-environment setup guide (clean clone → first answered request).
> - **[PROGRESS.md](PROGRESS.md)** — the live state ledger (most recent work at top).
> - **[10-build-roadmap.md](10-build-roadmap.md)** — the full phase plan.

---

## 1. What Wolf is (60 seconds)

Wolf is a self-hosted, multi-organization **agentic AI layer for Wazuh**
(SIEM/XDR). Users chat with Wolf in a web dashboard; Wolf answers by
calling tools against the org's Wazuh Indexer + Server API, retrieving
from a pgvector RAG corpus, researching the live web (SearXNG), and —
uniquely — **acting** on Wazuh through a capability-gated
propose → approve → execute → verify → undo pipeline. Every factual
answer is grounded: a local judge model verifies claims against evidence
and the UI chips each claim Verified / Uncertain / Not Verified / Non-factual.

Components (ADR 0016 naming; each ships as its own Debian package):

| Component | What | Port (dev) |
|---|---|---|
| **wolf-server** | FastAPI agent loop, tools, gateway, grounding | 7860 (HTTPS) |
| **wolf-dashboard** | Next.js web UI (+ stdlib TLS edge proxy, ADR 0023) | 3000 |
| **wolf-database** | Wolf-managed PostgreSQL 18 + pgvector cluster | 5432 |
| **wolf-search** | Self-hosted SearXNG (web research, ADR 0032) | 127.0.0.1:1307 |
| Ollama (external dep) | Local model runtime (chat / judge / embeddings) | 127.0.0.1:11434 |

The repo is **public** ([alsechemist on GitHub](https://github.com); hosted
CI on every push — operator decision 2026-06-12, do not re-propose
self-hosted runners).

---

## 2. Where the build stands (2026-07-12)

**Everything through Phase 6-f is SHIPPED and CI-green** at HEAD.
Quality state at wrap-up (re-proven fresh on 2026-07-12): **963 backend
tests passed / 0 skips**, `mypy --strict` across the strict set (117 files),
ruff clean, dashboard tsc + eslint clean.

### Shipped, in one breath

- **Phases 0–4 (closed):** monorepo foundations; provider-agnostic model
  abstraction (Ollama + OpenRouter + failover chain, ADRs 0004/0030/0031);
  the read path end-to-end (agent loop, tool calls, SSE streaming, citations);
  RAG layer (pgvector hybrid search: vector + FTS + RRF, aux embedder);
  multi-organization hardening (forced org filters, cross-org isolation gate).
- **Phase 5 (closed):** deployment substrate — component split per ADR 0016,
  native Debian packaging (5 .debs + meta), wolf-database CLI, native HTTPS +
  wolf-cert, the Claude-grade chat UI (progressive reveal, live activity feed,
  four grounding chips, conversation tree), release engineering (signed APT repo).
- **Phase 6 core (shipped):** capability-driven action pipeline — propose →
  validate → capability pre-flight → approve (SoD) → execute → verify → audit,
  with reversal + provenance recall + timed auto-reversal (ADRs 0025/0027/0028).
  Four action classes live: **active_response**, **agent_action** (group
  assign/remove, API-inverse undo), **rule_tuning** (snapshot-restore),
  **config_change** (snapshot-restore, deployment-aware per-cluster-node
  application — ADR 0029 + 6-f.6).
- **Phase 6.4:** tenant → organization rename (canonical everywhere).
- **Phase 6.5:** bootstrap Superuser "Wolf", per-org RBAC, login UX,
  same-network gate (edge proxy, OFF by default).
- **Phase 6.6:** Superuser-owned Wazuh component mapping; credential-driven
  per-org scoping (Wazuh RBAC + DLS), web-tested on the real 3-node cluster.
- **Phase 6-f (ADR 0032, shipped 6-f.1 → 6-f.6):** web research as a
  universal power — `web_search` / `web_fetch` / `web_crawl` live with full
  security taxonomy (SSRF guard, untrusted-content envelope, budgets),
  SearXNG self-hosted default (wolf-search .deb), docs-first re-rank,
  citations in the evidence panel; config-authoring generalization
  (block-identity upsert/remove, two-phase confirm-diff, research-to-act
  posture); no hard step caps (persist-until-satisfied); deployment-aware
  config application.
- **ADR 0033 (shipped 2026-07-11):** fully configurable embedding stack —
  every knob per-embedder (dimension, MRL, prefixes, num_ctx, char limits),
  DB columns follow settings via `embedding_schema --apply` (resumable
  retype + re-embed), **no-cap 4096-dim** via binary-quantization HNSW +
  exact rerank, `embedding_bench` comparison harness. Live machine runs the
  **measured-best** config: qwen3-embedding at native 4096 primary +
  nomic-embed-text-v2-moe 768 aux (MRR@10 0.963 vs 0.766 for the nomic combo).
- **PostgreSQL 18 (shipped 2026-07-11):** full PG17 → PG18 replacement —
  code gate (`REQUIRED_MAJOR_VERSION = 18` rejects 17), packaging, CI images
  (`pgvector/pgvector:pg18`), docs; live dev cluster upgraded to 18.4 +
  pgvector 0.8.5 on :5432, PG17 removed.

For the full narrative, read [PROGRESS.md](PROGRESS.md) top-down (newest first)
and [CHANGELOG.md](CHANGELOG.md) (append-only history).

### Live dev machine state at pause

- **Services:** `wolf-server.service` + `wolf-dashboard.service` (user-level
  systemd units, `systemctl --user`), `wolf-search.service` (system unit,
  127.0.0.1:1307). wolf-database cluster on :5432 (PostgreSQL 18.4, pgvector 0.8.5).
- **Models (Ollama):** chat/judge default posture = OpenRouter
  `cohere/north-mini-code:free` primary with **failover to local `qwen3:8b`**
  (`FALLBACK_MODEL_*`); embeddings `qwen3-embedding:latest` (4096, num_ctx 2048)
  + `nomic-embed-text-v2-moe` (768 aux). OpenRouter free tier = hard 50 req/day
  per account (ADR 0031) — local Ollama is the reliable path.
- **Corpus:** 5,182 knowledge chunks, fully embedded both columns, BQ HNSW
  index on the 4096 primary, cosine HNSW on the 768 aux.
- **Wazuh:** operator's external 3-node cluster at 192.168.250.2–9
  (indexers ×3, server master + 2 workers, dashboards ×2). Orgs `acme` and
  `beta` with dedicated scoped `wolf-<org>` Wazuh users.
- **Benchmark artifacts:** `.local/embedding_bench_queries.json` (100 cached
  questions), `.local/embedding_bench_report.txt` (local-only, gitignored).

---

## 3. What comes next (the queue, in order)

Nothing is half-finished — every slice through 6-f.6 + ADR 0033 is closed.
The queue below is what the operator gated or sequenced "later":

1. **6-f.4 operator web-test (pending):** virustotal `<integration>` upsert
   end-to-end on the live cluster. Needs the admin (`wazuh-wui`)
   credential (`manager:update_config`) and **qwen3:8b** as the chat model
   (the live `cohere/north-mini-code:free` emitted tool calls as prose —
   ADR 0031 reality).
2. **Nemotron model-switch evaluation (operator-gated):** if
   `cohere/north-mini-code:free` isn't right for agentic actions, evaluate
   Nemotron 3 Ultra (free) then Nemotron 3 Super (free) on OpenRouter —
   verify exact IDs against the live catalog + tool-call probe + graded
   `KNOWN_MODELS` entry before flipping `.env`. See memory
   `model-switch-nemotron-after-slices`.
3. **Phase 6.9 → 6.7 → 6.8:** outbound SMTP email (ADR 0022) **before**
   notification infrastructure, so notifications ship with an email channel;
   then SSE push. Notifications strictly isolated from audit/logs.
4. **Phase 6.10:** Superuser config-settings system (ADR 0019; DB
   source-of-truth, web ⇄ CLI ⇄ env three-way sync, per-component config
   planes, `wolf-tune` privileged helper). Consumers already waiting:
   same-network-gate toggle, model posture, grounding mode, `AGENT_STEP_BREAKER`.
5. **Phase 6.11 / 6.12:** Wolf-assisted Wazuh RBAC provisioning (Superuser-only,
   Wolf's first write authority over Wazuh security config — needs its own ADR);
   cross-role assistance/escalation.
6. **Phase 6.13 (committed, operator-sequenced):** grounding enrichment —
   more Verified verdicts *accurately* (never loosening the judge); source-tier-aware
   web evidence, per-claim evidence selection, calibration harness
   (`embedding_bench` is its seed). Own ADR when opened.
7. **Phase 7+:** cases/reporting → detection engineering → playbooks →
   wolf-hunt / wolf-den / wolf-pack (Phase 12) → optional auto-execution
   (Phase 13). See [10-build-roadmap.md](10-build-roadmap.md).

---

## 4. Standing rules in force (operator directives)

These are permanent until the operator revokes them. Full detail lives in
[`memory/`](../memory/) (one file per rule; `memory/MEMORY.md` is the index).

**Foundational posture**
- **Wolf unrestricted / full power:** Wolf is NOT read-only; restriction comes
  from the Wazuh credential's own RBAC, never from Wolf limiting itself.
- **Web research as universal power:** research-to-act like Claude — unknown
  procedure → docs-first research with citations → act via the capability-gated
  pipeline. Authority model unchanged (RBAC + approval gateway gate every write).
- **No hard step caps:** the agent loop persists until the answer; stops are
  the no-progress guard, the context-fit guard, and best-effort synthesis.
- **Single-org ↔ MSSP parity:** anything achievable multi-org must also work
  single-org (one broad credential, no DLS).
- **Superuser config authority:** all Wolf management/config is Superuser-only;
  org management → org admins; user settings → users.
- **Web-first configurability:** every configurable knob gets a GUI surface;
  CLI ⇄ GUI sync via DB as source of truth; every config change audited.

**Engineering discipline**
- **No unaddressed errors:** never leave errors/fails/warnings/skips; fixed at
  the root, never filtered or baselined. **Zero test skips of any kind** —
  stub the boundary instead.
- **Integrity across the stack:** every change preserves frontend/backend/DB/
  library/UI integrity; full backend suite + cross-org isolation gate on every
  `services/` change.
- **Quality + secure coding inline** per slice; dedicated hardening pass
  deferred but tracked.
- **Scope + validation discipline:** interrogate every parameter/field/scope;
  verify real-system behavior empirically before designing; every
  behavior-affecting input reflected in its test surface.
- **Input validation everywhere:** server-authoritative + client-inline, guided
  field-relevant messages.
- **CI audit before push:** audit `.github/workflows/` for slice-relevant
  changes and land them in the same commit; watch the run to green
  (background `gh run watch`, never foreground polling).
- **Periodic plan-sync:** audit roadmap/architecture/ADRs/PROGRESS for drift
  between phases; surface findings unprompted.
- **Shell-wrapper-required pattern:** every supporting tool = Python core +
  shell wrapper (audit, permissions, env preflight).
- **Memory mirrored into repo:** every memory lives in BOTH
  `~/.claude/projects/.../memory/` and in-repo `memory/`, byte-identical.
- **Per-slice web-test checkpoints:** reset → Claude self-validates → reset →
  operator manually tests. Restart wolf-server via
  `systemctl --user restart wolf-server.service` (never `pkill -f`).
- **Graphify-first:** when `graphify-out/graph.json` exists, use graphify
  query/path/explain for architectural questions before grep/Read sweeps.
- **UI/UX bar:** Claude's conversation UI/UX is the standard — dynamic,
  responsive, attractive, robust, applied strictly.

**Security / operations**
- **Sudo:** never accept the operator's password. Privileged steps run under an
  **announced temporary sudoers grant** (`/etc/sudoers.d/`), used only for the
  announced steps, then removed and verified (`sudo -n true` must fail).
  Recommend scoped (command-listed) grants over `NOPASSWD: ALL`.
- **Secrets:** `.env`, `credentials/`, `.local/` are gitignored and never
  committed. Wazuh credentials live in the encrypted secrets backend, never
  the DB. The live SearXNG `settings.yml` secret never enters git.
- **Never** `rm -rf .next` or `npm run build` while wolf-dashboard (next dev)
  runs; clear `.next` only while stopped (memory `next-dev-cache-vs-build`).

---

## 5. Decisions — where to look

Every architectural decision is an ADR under
[`docs/decisions/`](decisions/) (0001–0033, indexed in its README).
The load-bearing ones for current work:

- **0016** component naming/split · **0008** native-primary packaging ·
  **0023** dashboard edge proxy · **0019** config-settings (Phase 6.10)
- **0024** model postures · **0026** grounding modes · **0030/0031**
  OpenRouter + failover chain
- **0025/0027/0028/0029** the action pipeline + reversal + multi-class
- **0032** web research + config authoring · **0033** configurable embedding
  stack (+ BQ no-cap addendum + benchmark addendum)

Architecture docs `00`–`17` under `docs/` are the stable reference; the
reference dir `docs/reference/` holds operational guides
(model-performance-tuning, wazuh-active-response, restart).

---

## 6. Development environment — how to rebuild it

**[ONBOARDING.md](../ONBOARDING.md) is the canonical guide** — the complete
path from clean clone to first answered request: system requirements
(Ubuntu 24.04, Python 3.13 via uv, Node 24, PostgreSQL 18 + pgvector,
Ollama), wolf-database init, dev secrets, `.env`, migrations, model pulls,
organization bootstrap, service startup, seeding the knowledge corpus, and
the packaging/release path. Follow it step by step; do not skip.

Quick reference of the moving parts you'll need installed:

1. `uv` + Python 3.13 → `uv sync` at repo root (whole workspace).
2. Node 24 → `npm install` in `services/dashboard/`.
3. PostgreSQL 18 + pgvector (pgdg `postgresql-18` + `postgresql-18-pgvector`)
   → `make wolf-database-init` (prints the DATABASE_URL).
4. Ollama → pull `qwen3:8b` (chat/judge), `qwen3-embedding:latest` +
   `nomic-embed-text-v2-moe` (embedding stack; or the nomic combo — see
   `.env.example` recipes A/B).
5. `.env` from [.env.example](../.env.example) — see §7 for credentials.
6. `cd services/server && uv run alembic upgrade head`.
7. `uv run python -m wolf_server.management.bootstrap_organization …`
   (Wazuh URLs + credentials from `credentials/`).
8. Seed knowledge: `management/seed_dev_knowledge.py`; reconcile embedding
   schema if you changed embedder config: `management/embedding_schema --apply`.
9. Start services (`systemctl --user` units or terminals per ONBOARDING §3.10).
10. Verify: `curl -k https://127.0.0.1:7860/api/v1/auth/login` → 401 means up;
    `wolf-search health`; full gates = `uv run ruff check .`, `make typecheck`,
    `cd services/server && uv run pytest -q`.

---

## 7. Credentials

Real credentials live in the **gitignored** `credentials/` directory on the
dev machine (they are per-deployment and the operator reuses them).
The repo tracks **[credentials.example/](../credentials.example/)** — a
placeholder template of every credential file the project needs, each field
annotated with what to fill in and where it comes from:

- `wazuh-credentials.txt` — cluster URLs (indexer/server/dashboard),
  `admin` + `wazuh-wui` API users, per-org `wolf-<org>` scoped users.
- `postgresql-credentials.txt` — the wolf DB role password
  (printed by `make wolf-database-init`).
- `openrouter-credentials.txt` — OpenRouter account + API key
  (only if using a hosted model; local Ollama needs none).
- `wolf-credentials.txt` — Wolf app logins: the bootstrap Superuser "Wolf"
  (password autogenerated at install), org admin users.

Copy `credentials.example/` → `credentials/` and fill in real values; then
feed them to `bootstrap_organization` (Wazuh), `.env` (DATABASE_URL,
`DEFAULT_MODEL_API_KEY_REF` secret), and the login page (Wolf users).

---

## 8. The knowledge graph (graphify)

`graphify-out/` (repo root) holds a code knowledge graph regenerated
**locally on every commit** by the post-commit hook; it is normally
**not committed** (operator decision 2026-06-16 — graph churn polluted
diffs). **One-time exception at this wrap-up (operator request):** the
current graph artifacts — `graphify-out/graph.json`, `GRAPH_REPORT.md`,
`manifest.json` — were force-added once so a fresh clone on a new device
starts with the graph already present (the daily-snapshot dirs and the
43 MB rebuild cache stay local-only; they're redundant/regenerable). The
gitignore rule remains in force — future rebuilds stay uncommitted. On a
new machine you can also regenerate with the `/graphify` skill
(`~/.claude/skills/graphify/SKILL.md`) or let the post-commit hook rebuild.
Once `graphify-out/graph.json` exists, the graphify-first rule applies:
architectural questions go through graphify query/path/explain before
grep/Read sweeps.

---

## 9. Resuming with Claude Code

Paste the prompt in **[CLAUDE-RESUME-PROMPT.md](CLAUDE-RESUME-PROMPT.md)**
into a fresh Claude Code session started in the repo root. It instructs the
session to load this handover, PROGRESS.md, the memory index, and the
roadmap, re-verify the environment, and pick up at the top of the queue in
§3 — in that order, with all standing rules in force.
