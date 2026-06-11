# 09 — Tech Stack and Repository Layout

This document specifies concrete technologies and versions for Wolf, current
as of **May 2026**. Every choice is **open-source and self-hostable** — in line
with the project's core principle that the entire platform can run without paid
dependencies.

## Version policy

The rule for this project is **latest LTS where an LTS exists, latest mature
stable where it doesn't, with stability and feature-richness for *this
project's needs* as the tiebreaker.** Not "the newest version that exists."

In practice that means:

- We adopt an LTS once it has been Active LTS for at least a couple of months
  and library support is broad.
- We don't chase the *Current* (non-LTS) Node release.
- We don't chase a brand-new PostgreSQL major; the database is the most
  boring component in the stack and we want it that way.
- We don't chase a Python feature release that just shipped — we wait until
  the ML/embeddings/SDK ecosystem catches up, which is usually 6-12 months.
- Versions are pinned in lockfiles (`uv.lock`, `pnpm-lock.yaml`), updated via
  Renovate/Dependabot, and CI tests against both the pinned version and the
  next-major-when-available so upgrade-readiness is continuously known.

## The stack — pinned versions (May 2026)

### Runtimes

| Component | Version | Why this version |
|---|---|---|
| **Python** | **3.13.x** | Mature, every major library supports it, stable for ML and async workloads. 3.14 (Oct 2025) is acceptable if the team prefers latest, but 3.13 is the safer default for embeddings and ML libraries that lag the latest interpreter by ~6 months. Avoid 3.15 (alpha). |
| **Node.js** | **24.x LTS** | Active LTS through April 2028. Required for Next.js 16 and the JS toolchain. 26 is Current (not LTS yet); 22 is Maintenance. |
| **PostgreSQL** | **17.x** | One year of production hardening past release. Deliberately *not* PG 18 (released Sept 2025) — the database must be the most boring, most-deployed-by-others component in the stack. PG 17 is fully supported through 2029. Upgrade to 18 in year two once it has the same operational track record. |

### Backend

| Component | Version | Notes |
|---|---|---|
| **FastAPI** | latest 0.115+ | Async, typed contracts, OpenAPI generation. |
| **Pydantic** | 2.x latest | Strict tool I/O schemas (`03`). |
| **uv** | latest | Python package and venv manager. Replaces pip+venv+pip-tools — substantially faster, deterministic lockfile, manages Python versions. |
| **uvicorn** | latest | ASGI server. |
| **httpx** | latest | Async HTTP client for the Wazuh Server API. |
| **opensearch-py** | latest | Async OpenSearch client for the Indexer. |
| **SQLAlchemy** | 2.x latest | ORM/query layer. Pairs cleanly with Pydantic via SQLModel if preferred. |
| **Alembic** | latest | Schema migrations. |
| **Authlib** | latest | OIDC client. |
| **structlog** | latest | Structured JSON logging. |
| **OpenTelemetry SDK** | latest | Distributed tracing across the agent loop. |
| **pytest** + **pytest-asyncio** | latest | Tests. |
| **ruff** | latest | Lint + format. |
| **mypy** | latest | Strict-mode type checking on safety-critical packages. |

### Frontend

| Component | Version | Notes |
|---|---|---|
| **Next.js** | **16.x LTS** | Released Oct 2025, now Active LTS. Turbopack is the stable default bundler (2-5× faster builds, ~10× faster Fast Refresh), `proxy.ts` replaces `middleware.ts` as the network-boundary entry point (the natural home for organization context resolution), React Compiler is stable, Node-runtime middleware is stable. All directly relevant to this project. |
| **React** | **19.x** | Ships with Next 16. No independent choice. |
| **TypeScript** | **5.x latest** | Strict mode mandatory. |
| **Tailwind CSS** | **4.x** | Current major; supported by shadcn/ui's current components; well-adopted by mid-2026. |
| **shadcn/ui** | latest | Component foundation. |
| **lucide-react** | latest | Icon set. |
| **pnpm** | latest | Package manager (faster, stricter than npm). |
| **Vitest** | latest | Unit tests. |
| **Playwright** | latest | End-to-end tests. |
| **ESLint** + **Prettier** | latest | Lint + format. |

### Data layer

| Component | Version | Notes |
|---|---|---|
| **PostgreSQL** | **17.x** | Organizations, users, cases, proposals, configuration, audit. |
| **pgvector** | 0.8.x latest | PostgreSQL extension for vector search. v1 default vector store. |

A `VectorStore` interface sits in front of pgvector so a future swap to
**Qdrant** (medium-large scale) is a single-adapter change. **OpenSearch as a
vector store** is also acceptable but **must be a separate cluster from
Wazuh's** — coupling availability would violate `01`.

### RAG / embeddings

| Component | Version | Notes |
|---|---|---|
| **sentence-transformers** | latest | Local embedding model runtime, CPU-friendly. |
| **BAAI/bge-base-en-v1.5** | model | Default embedding model. Use `bge-large-en-v1.5` if RAM permits. |
| **rank-bm25** *or* PostgreSQL FTS | latest | Keyword side of hybrid retrieval. |

### LLM runtime and adapters

| Component | Version | Notes |
|---|---|---|
| **Ollama** | latest | Local model runner. Bundled in the dev/single-host compose. |
| **anthropic** SDK | latest | Claude adapter. |
| **openai** SDK | latest | OpenAI adapter (and used by the generic OpenAI-compatible adapter for vLLM, LM Studio, LocalAI, OpenRouter, etc.). |
| **google-genai** SDK | latest | Gemini adapter. |
| **DeepSeek** | latest | Via DeepSeek SDK or its OpenAI-compatible endpoint. |

Recommended default local models the platform must test against:

- **Llama 3.3 8B** — default mid-tier capable model.
- **Qwen 3 8B** — alternative mid-tier.
- **Gemma 3 4B** — small/basic-tier model for low-RAM hosts.

### Identity, secrets, observability

| Component | Version | Notes |
|---|---|---|
| **Keycloak** | latest (runs on Java 21 LTS) | Recommended self-hosted OIDC IdP. |
| **OpenBao** *or* **HashiCorp Vault** | latest | Production secrets manager. **OpenBao** is the truly-open fork — the recommended default to align with the "fully open-source" principle. Vault acceptable if operator prefers. |
| **Prometheus** | latest | Metrics. |
| **Grafana** | latest | Dashboards. |
| **OpenTelemetry Collector** | latest | Trace export. |

### Delivery channels and build tooling

Wolf is delivered via two channels — one primary, one supplementary —
per [ADR 0007](decisions/0007-native-distribution-via-system-packages-and-install-script.md)
and [ADR 0008](decisions/0008-native-primary-docker-supplementary.md).

**Primary: native system packages** (`.deb`/`.rpm` + systemd units,
fronted by a one-line install script). Specified in
[`docs/16-distribution-and-packaging.md`](16-distribution-and-packaging.md).
This is where operator-facing polish goes. Implementation is queued
for post-Phase 4.

**Supplementary: container images** (Dockerfiles + `docker-compose.yml`
in the repo). Baseline-supported, not promoted. Serves operators who
want to build their own images — typically for Kubernetes deployment
on infrastructure that expects containers. No polished `docker compose
up`-and-done experience is committed; no Helm chart investment is
committed near-term.

| Component | Version | Notes |
|---|---|---|
| **Docker Engine** | latest | Container runtime. Used by the supplementary container channel. |
| **Docker Compose v2** | latest | Single-host container stack. |
| **Kubernetes** | 1.30+ | For operators who build their own images on top of Wolf's Dockerfiles. Wolf does not ship k8s manifests today. |
| **GitHub Actions** | n/a | CI. (Or GitLab CI, or Forgejo Actions — the workflow files are runner-agnostic in principle.) |

The repo's [`Makefile`](../Makefile) marks which targets serve which
channel. Native-dev targets (`test`, `lint`, `typecheck`, `migrate-local`,
`probe`) are the day-to-day path; container targets (`up`, `down`,
`dev`, `logs`, `migrate`) build and run the container stack for
operators who want to use it.

### License

**Apache 2.0** — permissive, explicit patent grant, MSSP-friendly. GPL/AGPL are
deliberately rejected because they would block MSSP adoption, which the project
targets.

## Why two services (orchestrator and gateway)

This is deliberate and structural, not stylistic:

- The **orchestrator** handles model interaction. The model is exposed to
  attacker-controlled content (logs). The orchestrator is the most "exposed"
  service.
- The **gateway** executes state changes. Its credentials are the dangerous
  ones.

Keeping them as **separate services with separate credentials** means
compromising the orchestrator does not automatically grant the ability to
change endpoint state — the gateway still demands signed, hash-bound approval
tokens it issues itself.

A single-process convenience mode for tiny single-org deployments is
acceptable **only if it preserves the typed proposal + signed-token protocol
between the two layers internally**, so the service can be split later
without a rewrite.

## Repository layout

A monorepo with clear top-level boundaries between services and shared
libraries.

```
wolf/
├── README.md
├── LICENSE                       # Apache 2.0
├── SECURITY.md                   # Vulnerability disclosure policy
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── docs/                         # This planning bundle + user/operator docs
├── docker-compose.yml            # Single-host "everything" stack
├── docker-compose.dev.yml        # Dev overlays
├── Makefile                      # Common commands (test, run, build)
│
├── pyproject.toml                # Python project root (managed by uv)
├── uv.lock                       # Pinned Python dependencies
├── .python-version               # 3.13.x — read by uv
│
├── pnpm-workspace.yaml           # JS workspace
├── .nvmrc                        # 24 — Node LTS
│
├── .github/
│   └── workflows/                # CI: tests, isolation suite, security scans
│
├── services/
│   │
│   ├── orchestrator/             # Python FastAPI — the agent orchestrator
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── api/
│   │   │   ├── agent/            # Agent loop, strategies (frontier/mid/basic)
│   │   │   ├── tools/            # READ + PROPOSE tools ONLY
│   │   │   │   ├── registry.py
│   │   │   │   ├── read/
│   │   │   │   └── propose/
│   │   │   ├── models/           # Model abstraction layer + adapters
│   │   │   │   ├── interface.py
│   │   │   │   ├── anthropic.py
│   │   │   │   ├── openai.py
│   │   │   │   ├── gemini.py
│   │   │   │   ├── deepseek.py
│   │   │   │   ├── ollama.py
│   │   │   │   └── generic_openai.py
│   │   │   ├── tenancy/          # Organization context + enforcement helpers
│   │   │   ├── auth/
│   │   │   ├── audit/
│   │   │   ├── rag/              # Knowledge layer, retrieval, ingestion
│   │   │   └── cases/            # Case orchestration
│   │   └── tests/
│   │
│   ├── gateway/                  # Python FastAPI — Approval & Action Gateway
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── proposals/        # Lifecycle, state machine
│   │   │   ├── execute/          # The actual EXECUTE tools live ONLY here
│   │   │   ├── approval/         # Token issuance, authority checks
│   │   │   └── verify/           # Verification reads after execution
│   │   └── tests/
│   │
│   └── frontend/                 # Next.js 16 app (App Router)
│       ├── app/
│       │   ├── (auth)/           # Login / OIDC callback
│       │   ├── (app)/            # Authenticated routes
│       │   │   ├── layout.tsx    # Sidebar, organization picker
│       │   │   ├── cases/
│       │   │   ├── approvals/
│       │   │   ├── reports/
│       │   │   ├── knowledge/
│       │   │   └── admin/
│       │   ├── api/              # Next API routes — UI-internal ONLY
│       │   └── layout.tsx
│       ├── components/
│       ├── lib/
│       │   ├── orchestrator-client.ts  # Server-only client to the orchestrator
│       │   └── session.ts
│       ├── proxy.ts              # Organization context + session validation
│       ├── next.config.ts
│       ├── package.json
│       └── tsconfig.json
│
├── packages/                     # Shared libraries
│   ├── schema/                   # Pydantic models, tool schemas, proposal schema
│   ├── wazuh-client/             # OpenSearch + Server API clients with organization injection
│   ├── secrets/                  # Secrets-backend abstraction (OpenBao, file)
│   └── common/                   # Logging, tracing, error taxonomy
│
├── deploy/
│   ├── docker/                   # Dockerfiles
│   ├── k8s/                      # Manifests / Helm (later phase)
│   └── examples/                 # Reference deployments (single-org, MSSP)
│
└── tools/
    ├── model_probe/              # Capability self-test (see 02)
    ├── organization_isolation_test/    # Cross-organization negative test suite (see 05)
    └── seed_knowledge/           # Bootstrap Wazuh docs + ATT&CK into RAG
```

### Where `proxy.ts` sits in the security model

The Next.js 16 `proxy.ts` file replaces `middleware.ts` and runs before every
request at the network boundary — clearer naming for what is, in this project,
**the network-boundary security entry point.** This is where:

1. The session cookie is validated.
2. The user identity is resolved.
3. The active **organization context** is bound to the request (per `05`).
4. Unauthenticated requests are redirected to `/login`.

Organization context flows from `proxy.ts` into every Server Component and Server
Action through the Next request context, never read from the model's output and
never trusted from the client.

## What this stack costs to run, fully self-hosted

For a small single-org deployment:

- One VM with 16-32 GB RAM, 4-8 CPU cores, ~100 GB disk.
- A 7-13B local model via Ollama is viable on this hardware (CPU-only is
  slow but functional; a modest consumer GPU is a substantial improvement).
- Postgres 17, pgvector, Keycloak, OpenBao, Wolf — all fit comfortably.
- **Total ongoing software cost: zero.**

For an MSSP with many organizations or detection-engineering-heavy use:

- A larger machine or small cluster.
- Either a stronger local model on better hardware, or paid API access —
  operator's choice, never required by the platform.

See `13-system-requirements.md` for detailed hardware profiles.
