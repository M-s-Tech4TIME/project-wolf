# 13 — System Requirements

Concrete hardware and software requirements for developing and running
Wolf. Numbers are current as of May 2026.

## Developer machine

The minimum to be productive working on the codebase. The local LLM is
**optional** here — you can develop against a hosted API model and only run
Ollama occasionally to verify the local-model path.

### Minimum (tight but workable)

- 4 CPU cores
- 16 GB RAM
- 50 GB free SSD
- Linux, macOS, or Windows with WSL2
- Docker Desktop (or Docker Engine + Compose v2 on Linux)

### Recommended (comfortable)

- 8 CPU cores
- 32 GB RAM
- 100+ GB free SSD
- Same OS list

Concurrently the dev environment runs: orchestrator, gateway, Postgres 17,
Keycloak, OpenBao (or file-backed secrets), optionally Ollama with a small
model, the Next.js dev server, IDE, browser. 16 GB is doable; 32 GB is
genuinely comfortable.

### Required software on the developer machine

| Tool | Version | Install via |
|---|---|---|
| **Python** | 3.13.x | `uv python install 3.13` |
| **uv** | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **Node.js** | 24 LTS | `nvm install 24 && nvm use 24` (read from `.nvmrc`) |
| **pnpm** | latest | `corepack enable && corepack prepare pnpm@latest --activate` |
| **Docker Engine + Compose v2** | latest | OS package manager / Docker Desktop |
| **Git** | latest | OS package manager |

`make bootstrap` (provided in the repo) sets all of this up after the initial
Docker install.

## Runtime server — four profiles

This is where the model-agnostic story becomes concrete. Pick the profile that
matches the available hardware; the platform's `model_probe` measures the
chosen model and the orchestrator picks the matching strategy (`02`).

### Profile A — CPU only, frugal (small VPS or test box)

**Hardware:**
- 8 CPU cores (AVX2 required; AVX-512 ideal — Intel 12th Gen+ or AMD Zen 4)
- 16 GB RAM (32 GB strongly recommended)
- 100 GB SSD

**Realistic model:** a 3-4B model like Gemma 3 4B. CPU-only on 16 GB RAM is
limited to 3-4B models; CPU-only on 32 GB RAM makes 7B models usable but slow
(3-5 tokens/second).

**Platform strategy:** `basic` — deterministic pipeline with model-in-the-slots.
Reliable but tightly scaffolded.

### Profile B — Modest GPU, the sweet spot (recommended starting point)

**Hardware:**
- 8 CPU cores
- 32 GB RAM
- A GPU with **6-8 GB VRAM** (RTX 3060/4060 class, or equivalent)
- 200 GB SSD

**Realistic model:** 7-9B models like Llama 3.3 8B or Qwen 3 8B at Q4_K_M
quantization, delivering 40+ tokens/second.

**Platform strategy:** `mid` — guided agent with checkpoints.

### Profile C — Strong local model (MSSP or detection-engineering-heavy)

**Hardware:**
- 12+ CPU cores
- 64 GB RAM
- A GPU with **24 GB VRAM** (RTX 3090/4090/5090, or two 12-16 GB cards)
- 500 GB SSD

**Realistic model:** 22-35B models like Qwen 3 32B or DeepSeek-R1 32B at
Q4_K_M.

**Platform strategy:** `mid`, sometimes `frontier` depending on the model.

### Profile D — No local model, hosted API only

**Hardware:**
- 8 CPU cores
- 16-32 GB RAM
- 100 GB SSD
- **No GPU needed.** All inference happens at the provider.

**Platform strategy:** whatever the provider's model rates as
(Claude/GPT/Gemini → `frontier`).

**Trade-offs:** cost per token, plus log data leaves the operator's
infrastructure (matters for MSSP tenants under data-residency obligations —
see `07`).

## Quality gate — Profile B must work

> The platform must continue to function and pass its test suite on Profile B
> hardware with a local model. This is enforced by CI.

Profile B compatibility is the empirical proof of the "no paid subscription
required" promise. Without it, the model-agnostic claim is theater. The CI
must run at least the read-path tests against an Ollama-backed local model on
runners sized to Profile B (8 GB VRAM equivalent or CPU fallback).

## What the platform deploys

Container images for:

| Image | Purpose |
|---|---|
| `wolf/orchestrator` | The agent orchestrator service (FastAPI). |
| `wolf/gateway` | The Approval & Action Gateway (FastAPI). |
| `wolf/frontend` | Next.js 16 app, server-side rendered. |
| `postgres:17` | Relational store. |
| `pgvector/pgvector:pg17` | Postgres + pgvector. (Same image, vector-enabled.) |
| `quay.io/keycloak/keycloak:latest` | OIDC IdP. |
| `openbao/openbao:latest` | Secrets manager (or file-backed for trivial deploys). |
| `ollama/ollama:latest` | Local model runtime (optional). |
| `prom/prometheus` + `grafana/grafana` | Observability (optional). |

A single `docker compose up` on Profile B hardware brings the whole stack
online with a default tenant ready for first login.

## Network and connectivity requirements

The platform requires outbound network access to:

- The configured Wazuh Indexer endpoint (the tenant's OpenSearch).
- The configured Wazuh Server API endpoint.
- The configured LLM endpoint (Ollama on localhost, or a hosted API).
- Configured threat-intel feeds (optional).

It does **not** require general outbound internet by default; egress is
restricted to the configured endpoints (`07-security-and-threat-model.md`).

Inbound: the frontend port (default 3000) and, if exposed separately, the
orchestrator API port. The gateway should not be exposed externally.

## Upgrade paths

- **Python 3.13 → 3.14** — supported once embedding-library compatibility is
  confirmed across `sentence-transformers`, `torch`, and the LLM SDKs. CI
  should run a "next-Python" matrix.
- **Node 24 → 26** — once Node 26 enters LTS (Oct 2026).
- **PostgreSQL 17 → 18** — once PG 18 has at least a year of production-broad
  deployment. Migration is `pg_upgrade`-supported.
- **Next.js 16 → 17** — when the next Next LTS lands.

All upgrades are tested via CI's next-major matrix before being adopted.
