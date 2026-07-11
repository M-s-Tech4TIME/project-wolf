# Wolf — Onboarding (Start Here)

> **You are a new contributor (human or AI) who has just cloned this
> repo.** This file gets you from `git clone` to a working dev
> environment with a chat session against a real Wazuh, then orients
> you for whatever phase of work is next.
>
> **Read this file end-to-end before doing anything else.** Then read
> the documents it points you at, in the order it specifies.
>
> **Resuming after the 2026-07-12 development pause?** Read
> [`docs/HANDOVER.md`](docs/HANDOVER.md) first — the wrap-up snapshot
> (state, queue, standing rules, credentials) — then come back here for
> the environment setup. Claude Code sessions: paste
> [`docs/CLAUDE-RESUME-PROMPT.md`](docs/CLAUDE-RESUME-PROMPT.md).

**Last verified:** 2026-06-03 against `origin/main` after Phase 5.5
(component-renaming refactor — frontend → wolf-dashboard,
orchestrator → wolf-server, app/ → wolf_server/ + wolf_gateway/).
The repo is moving; if commands here drift from reality, trust the
code, then fix this file in your first commit.

---

## 0. Sixty-second orientation

**Wolf** is an open-source, model-agnostic, agentic AI platform that
sits *beside* a Wazuh deployment (Indexer + Server API) and helps
analysts, detection engineers, and MSSPs operate it. It reads freely,
proposes state-changing actions, and never executes them without an
authenticated human approval. The full pitch is in
[`README.md`](README.md) and [`docs/00-vision-and-scope.md`](docs/00-vision-and-scope.md).

The codebase is divided into three deployable components per ADR
0016 (`wolf-dashboard`, `wolf-server`, `wolf-database` — Phase 5.7;
plus `wolf-gateway` for Phase 6) plus shared packages and tooling:

```
project-wolf/
├── docs/                  # 16 numbered planning docs + decisions/ (ADRs) + PROGRESS.md + CHANGELOG.md
├── packages/              # Shared Python libraries (common, secrets, schema, cert)
├── services/
│   ├── dashboard/         # wolf-dashboard — Next.js 16 edge component (the only one browsers talk to)
│   ├── server/            # wolf-server — FastAPI brain (agent loop, tools, auth, audit)
│   └── gateway/           # wolf-gateway — Phase 6 propose/execute path (stub today)
├── bin/                   # Shipped operator CLIs (wolf-cert; future wolf-status, wolf-backup)
├── tools/                 # Dev-internal CLIs (model_probe, seed_knowledge, organization_isolation_test)
├── deploy/                # Dockerfiles, Compose, future systemd units + packaging
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
- Touching tenancy or auth? → [`docs/05-multi-organization.md`](docs/05-multi-organization.md), [`docs/07-security-and-threat-model.md`](docs/07-security-and-threat-model.md).
- Starting Phase 3 (RAG)? → [`docs/06-knowledge-and-rag.md`](docs/06-knowledge-and-rag.md), [`docs/10-build-roadmap.md`](docs/10-build-roadmap.md) §"Phase 3".
- Setting up new hardware? → [`docs/13-system-requirements.md`](docs/13-system-requirements.md).
- Working on distribution / packaging (post-Phase 4)? → [`docs/16-distribution-and-packaging.md`](docs/16-distribution-and-packaging.md), [ADR 0007](docs/decisions/0007-native-distribution-via-system-packages-and-install-script.md).
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
- **`npm`** (ships with Node 24) — used to install the dashboard's dependencies in [`services/dashboard/`](services/dashboard/).
- **PostgreSQL 18 + pgvector** — installed natively via your distro's package manager. Recommended dev path; matches the production install per [ADR 0008](docs/decisions/0008-native-primary-docker-supplementary.md). See §3.4 for install steps. (Docker Postgres is a supported alternative — also documented in §3.4.)
- **Ollama** — local model runtime. Install: `curl -fsSL https://ollama.com/install.sh | sh`. https://ollama.com.

### Optional

- **Docker + Docker Compose v2** — only needed if you choose the alternative dev path of running Postgres in Docker, or if you want to exercise the supplementary container channel (`make up`). See [ADR 0008](docs/decisions/0008-native-primary-docker-supplementary.md) for why Docker is supplementary rather than primary.
- **A GPU** — drastically improves model latency. The four-family matrix in [`docs/15-supported-model-matrix.md`](docs/15-supported-model-matrix.md) expects workstation-GPU hardware (24+ GB VRAM) to be fully exercised. CPU-only is the floor, not the ceiling.
- **A reachable Wazuh deployment** — Indexer (default :9200) and Server API (default :55000). Required for live-data smoke tests, not for unit tests.

### Network ports used by the dev stack

| Port | Component | Bound | Notes |
|---|---|---|---|
| 7860 | wolf-server (FastAPI) | 0.0.0.0 | LAN-reachable for browser access |
| 8001 | wolf-gateway (FastAPI) | 0.0.0.0 | Stub today; will be needed Phase 6+ |
| 3000 | wolf-dashboard (Next.js dev) | 0.0.0.0 | LAN-reachable |
| 5432 | wolf-database (Postgres 18 + pgvector; system Postgres pre-Phase 5.7) | 127.0.0.1 | System Postgres default (Docker alternative binds 0.0.0.0); becomes wolf-database in 5.7 |
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

# Sync all workspace packages (server, gateway, packages/*)
uv sync --all-packages
```

This creates `.venv/` at the repo root and installs everything in
editable mode.

### 3.3 Install wolf-dashboard deps

```bash
cd services/dashboard
npm install
cd ..
```

### 3.4 Install and start Postgres 18 + pgvector

Three supported paths. All three end in a working `wolf` database
with the `vector` extension installed; `DATABASE_URL` in `.env` is
the only contract wolf-server cares about.

#### Path A — wolf-database (recommended)

Wolf manages the Postgres lifecycle itself. The OS package manager
provides the Postgres + pgvector binaries; wolf-database owns the
config, data dir, socket, and start/stop. The chain wolf-cert →
wolf-database → wolf-server has been verified end-to-end as of
Phase 5.7-d's integration test. Phase 5.8 added systemd units so
the cluster auto-restarts on boot, closing the last UX gap
between Path A and "just works."

##### Dev — user-level systemd (no root needed for daily ops)

```bash
# 1. Install the Postgres binaries from the official PostgreSQL
#    APT repo (Ubuntu ships 16, not 18).
sudo install -d /usr/share/postgresql-common/pgdg
sudo curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
    https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" | \
    sudo tee /etc/apt/sources.list.d/pgdg.list
sudo apt update
sudo apt install -y postgresql-18 postgresql-18-pgvector

# 2. (One-time) STOP and DISABLE the system Postgres unit so it
#    doesn't fight wolf-database for port 5432. Debian/Ubuntu's
#    postgresql.service auto-starts at install; we want Wolf to
#    own the cluster.
sudo systemctl stop postgresql
sudo systemctl disable postgresql

# 3. Initialize the wolf-database cluster — one-shot setup. Lays
#    down a fresh cluster under <repo>/.local/wolf-database/,
#    creates the wolf role + db, installs pgvector, prints a
#    DATABASE_URL. Use --port 17860 if you want to keep the system
#    Postgres available on 5432 (don't disable it in step 2).
make wolf-database-init

# 4. Copy the printed DATABASE_URL line into your .env (replaces
#    the GENERATED placeholder in .env.example).

# 5. Install + enable the user-level systemd unit. After this,
#    wolf-database auto-starts whenever your user session does.
make install-user-systemd
systemctl --user enable --now wolf-database

# 6. For headless boxes — keep the user session alive across
#    logout / SSH disconnect, so wolf-database keeps running:
loginctl enable-linger $USER

# Lifecycle from here on:
systemctl --user status wolf-database      # is it running?
systemctl --user stop wolf-database        # stop
systemctl --user start wolf-database       # start
journalctl --user -u wolf-database --follow  # live log
```

On RHEL/Fedora replace `apt install postgresql-18
postgresql-18-pgvector` with `dnf install postgresql18 pgvector_18`
from the PostgreSQL YUM repo; everything after step 1 is
distro-independent.

##### Production — system-level systemd (root install)

When you're ready to deploy wolf-database on a real server (vs
your dev box), the production parity path is:

```bash
# 1. Same binary install as step 1 above (postgresql-18 +
#    postgresql-18-pgvector). Then stop + disable system Postgres
#    as in step 2.

# 2. Create system users + group + FHS dirs. Idempotent.
sudo bash deploy/systemd/system/install-users.sh

# 3. Install the shipped CLI shims + /usr/lib/wolf-*/ dirs.
sudo bash deploy/bin/install.sh

# 4. Drop the system-level unit files into place.
sudo cp deploy/systemd/system/wolf-*.service /lib/systemd/system/
sudo systemctl daemon-reload

# 5. Initialize wolf-database as the dedicated wolf-database user.
sudo -u wolf-database \
    WOLF_DATABASE_PRODUCTION=1 \
    /usr/bin/wolf-database init

# 6. Capture the printed DATABASE_URL from step 5; paste it into
#    /etc/wolf-server/env (mode 0640 wolf-server:wolf).

# 7. Enable + start all three services.
sudo systemctl enable --now wolf-database wolf-server wolf-dashboard
```

After step 7, journald captures all three components' output —
`journalctl -u wolf-database -f` etc. for live tailing. Auto-
restart on reboot is automatic via systemd. Phase 5.9 / 5.10 will
wrap steps 2-7 in a `.deb` / `.rpm` post-install hook so the
operator command becomes `apt install wolf` and nothing else.

The data dir lives under `/var/lib/wolf-database/data/` in
production (vs `<repo>/.local/...` in dev). All FHS paths
(`/var/lib/wolf-*/` data, `/etc/wolf-*/` config, `/var/run/wolf-*/`
sockets) follow the Wolf-owned + group-readable pattern from
ADR 0016.

#### Path B — System Postgres (still supported as a fallback)

Operators with existing Postgres infrastructure, or who'd rather
not introduce a new systemd unit on their dev box, can keep using
the distro's systemd-managed cluster. Wolf-server connects via
DATABASE_URL exactly the same way. Path A is the recommended
approach for new installs; Path B is here so existing setups
keep working.

```bash
# Same install as Path A step 1 (postgresql-18 +
# postgresql-18-pgvector). DON'T disable postgresql.service.

# Create the wolf role and DB by hand
sudo -u postgres psql <<EOF
CREATE ROLE wolf WITH LOGIN PASSWORD 'wolf_dev_password';
CREATE DATABASE wolf OWNER wolf;
\c wolf
CREATE EXTENSION IF NOT EXISTS vector;
EOF
```

The `DATABASE_URL` default in [`.env.example`](.env.example) covers
this exact case (`wolf:wolf_dev_password@localhost:5432/wolf`) —
no edit needed.

#### Path C — Docker Postgres (alternative, per ADR 0008)

Useful for macOS contributors, anyone running multiple
Postgres-using projects, anyone wanting fast reset via
`docker compose down -v`:

```bash
docker compose up -d postgres
```

Same `DATABASE_URL` works because the compose file binds Postgres
to `localhost:5432`. The codebase doesn't care which path you
choose; `DATABASE_URL` is the only contract.

### 3.5 Generate dev secrets

wolf-server needs two secrets in `.env`:

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
# Inside services/server so alembic finds its config.
cd services/server
uv run alembic upgrade head
cd ../..
```

You should see migrations `0001_initial_schema`, `0002_organization_wazuh_config`, `0003_inject_organization_filter` apply cleanly.

### 3.8 Install Ollama + pull the default model

```bash
# Install Ollama (skip if already installed)
curl -fsSL https://ollama.com/install.sh | sh

# Start the daemon (if not already)
ollama serve &     # or use systemctl on systems that have a service unit

# Pull the project's steady-state default (Apache 2.0, see ADR 0004)
ollama pull qwen3:4b

# Chat/judge model used on the dev machine (and the FALLBACK_MODEL_ID
# safety net when a hosted primary is configured — ADR 0031)
ollama pull qwen3:8b

# Embedding models — REQUIRED for the RAG layer. Pull the set matching
# the .env recipe you chose (.env.example, ADR 0033):
#   Recipe A (nomic combo):  nomic-embed-text + nomic-embed-text-v2-moe
#   Recipe B (qwen):         qwen3-embedding (+ optionally v2-moe as aux)
ollama pull nomic-embed-text
ollama pull nomic-embed-text-v2-moe
ollama pull qwen3-embedding:latest
```

For the broader supported-model matrix, see
[`docs/15-supported-model-matrix.md`](docs/15-supported-model-matrix.md).
On a GPU-equipped machine, also pull the larger sizes you intend to
exercise (e.g. `ollama pull qwen3:32b`, `ollama pull glm-5.1`).

### 3.9 Bootstrap a organization, admin user, and Wazuh connection

A single command creates the organization, admin user, the
`OrganizationWazuhConfig` row, and stashes the Wazuh credentials in the
encrypted secrets backend. **All Wazuh fields are required** — the
CLI cannot create a organization without them. If you don't have a Wazuh
handy yet, see "If you don't have a Wazuh yet" below.

```bash
cd services/server
uv run python -m wolf_server.management.bootstrap_organization \
    --organization-slug acme \
    --organization-name "Acme SecOps" \
    --admin-email admin@example.com \
    --admin-password 'choose-a-strong-password' \
    --opensearch-url https://wazuh.example:9200 \
    --opensearch-username wolf_ro \
    --opensearch-password '<indexer-password>' \
    --server-api-url https://wazuh.example:55000 \
    --server-api-username wolf_ro \
    --server-api-password '<api-password>' \
    --no-verify-tls
cd ../..
```

**Notes:**

- The command is **fully idempotent**. Re-running with the same
  `--organization-slug` updates URLs and re-stashes credentials in place;
  user/role bindings are preserved. This is also the supported way
  to *update* a organization's Wazuh config later — see §5 "Update an
  existing organization's Wazuh config."
- `--no-verify-tls` is correct for typical Wazuh deployments
  (self-signed certs from the default install). Use `--verify-tls`
  in production with proper PKI.
- Wazuh credentials are written to the secrets backend; they are
  never persisted to the database. The DB stores only the secret-key
  *reference* the resolver looks up at request time.
- Full flag reference: `uv run python -m wolf_server.management.bootstrap_organization --help`,
  or read the docstring at the top of
  [`services/server/wolf_server/management/bootstrap_organization.py`](services/server/wolf_server/management/bootstrap_organization.py).

**If you don't have a Wazuh yet:** pass placeholder values that satisfy
arg validation. The organization + auth + agent loop will all work; only
tool calls that actually hit Wazuh will fail at request time:

```bash
# Placeholder pattern — login works, tool calls will error on connect
--opensearch-url https://localhost:9200 \
--opensearch-username placeholder --opensearch-password placeholder \
--server-api-url https://localhost:55000 \
--server-api-username placeholder --server-api-password placeholder \
--no-verify-tls
```

### 3.10 Start the services

In two separate terminals (or use `nohup` / `tmux`):

```bash
# Terminal 1 — wolf-server
cd services/server
uv run python -m wolf_server

# Terminal 2 — wolf-dashboard
cd services/dashboard
npm run dev
```

Both launchers auto-detect TLS state (Phase 5.4-c / 5.4-d): when
the cert pair exists under `<repo>/.local/certs/{server,
dashboard}/{cert,key}.pem` they serve HTTPS; otherwise they fall
back to plain HTTP. The first line each prints reports which scheme
was picked. Run `wolf-cert init` (see §3.12) when you want to turn
on HTTPS; until then HTTP is the default.

**`cd` is still the cleanest invocation** — running the launchers
from the repo root works post-Phase-5.5 (the rename eliminated the
old "two `app/` packages collide" gotcha), but starting from
each component's own directory keeps the working directory pointed
at the right `.env` / config tree.

### 3.11 First request

```bash
# Health check
curl -fsS http://localhost:7860/healthz

# Login (saves cookie to /tmp/wolf-cookie.txt)
curl -fsS -c /tmp/wolf-cookie.txt -H 'Content-Type: application/json' \
    -d '{"email":"admin@example.com","password":"choose-a-strong-password"}' \
    http://localhost:7860/api/v1/auth/login

# Send a chat question
curl -fsS -b /tmp/wolf-cookie.txt -H 'Content-Type: application/json' \
    -d '{"question":"how many alerts in the last 24 hours by severity?"}' \
    http://localhost:7860/api/v1/chat
```

Or open wolf-dashboard at `http://localhost:3000` (or your LAN IP, e.g.
`http://192.168.1.50:3000`), log in, and chat from the UI.

### 3.12 Enable HTTPS + mTLS via `wolf-cert` (optional but recommended)

Plain HTTP works for everything functional, but browsers gate
"secure-context" APIs (clipboard, notifications, Web Crypto,
service workers) on a secure origin. Wolf ships a `wolf-cert` CLI
(Phase 5.4) that mints a self-signed CA + leaf certs in a single
command. Once installed in your OS / browser trust store, the
browser shows the green padlock and wolf-server + wolf-dashboard
both serve over HTTPS automatically.

**Phase 5.6 layered mTLS on top.** `wolf-cert init` now also mints
a `dashboard-client` leaf with `LeafKind.CLIENT`. After init,
wolf-server requires every non-/healthz request to present a
Wolf-CA-signed client cert whose Subject CN is in the configured
allowlist (default `wolf-dashboard-client`). wolf-dashboard's
reverse-proxy ([`app/api/[...path]/route.ts`](services/dashboard/app/api/[...path]/route.ts))
automatically presents this client cert on every outbound call.
End result: the browser sees one origin (the dashboard), and
wolf-server refuses any caller that isn't wolf-dashboard.

**The lifecycle:**

```bash
# 1. Mint the CA + three leaves (server, dashboard, dashboard-client)
#    under <repo>/.local/certs/. Default validity is 100 years —
#    the "practical infinity" pattern (RFC 5280 forbids truly
#    unlimited).
wolf-cert init

# 2. Inspect what was minted (you should see all three leaves).
wolf-cert status

# 3. Export the CA cert so you can install it in your OS / browser
#    trust stores (§ "Trust the Wolf root CA" below).
wolf-cert export-ca --out ./wolf-ca.crt

# 4. (Later) extend an existing cert's SAN list — e.g. when your
#    LAN IP changes or you add a new hostname.
wolf-cert add-host 192.168.42.7

# 5. (Later) reissue with fresh validity, or fully wipe + start over.
wolf-cert renew --years 100
wolf-cert revoke --yes      # deletes everything in .local/certs/
```

After `wolf-cert init`, restart wolf-server and wolf-dashboard —
their launchers see the cert pair and flip to HTTPS + mTLS
automatically. The startup banners report the picked mode:

```
wolf-server: serving https://0.0.0.0:7860
  TLS:  TLS cert+key present at .local/certs/server/{cert,key}.pem
  mTLS: ENABLED — Wolf CA at .local/certs/ca/ca-cert.pem;
        allowed client CNs: [wolf-dashboard-client]
```

```
wolf-dashboard: serving HTTPS via Next.js --experimental-https
  cert: .local/certs/dashboard/cert.pem
  key:  .local/certs/dashboard/key.pem
  proxy mTLS: ENABLED — presenting .local/certs/dashboard-client/cert.pem
              as the dashboard-client cert to wolf-server
```

If either banner shows `DISABLED` after a fresh `wolf-cert init`,
something is wrong — see §"Troubleshooting mTLS" below.

The Next.js dev server will also print a "Self-signed certificates
are currently an experimental feature, use with caution" notice on
startup; that's Next.js's own warning about its experimental flag,
not an indication of a problem with our certs.

**Trust the Wolf root CA on your machine.** Until you import the
CA, browsers will show "Your connection is not secure" even though
TLS is mathematically sound. One-time install per machine per OS:

#### Linux — Ubuntu / Debian-derived

```bash
sudo cp wolf-ca.crt /usr/local/share/ca-certificates/wolf-root-ca.crt
sudo update-ca-certificates
```

#### Linux — Fedora / RHEL-derived

```bash
sudo cp wolf-ca.crt /etc/pki/ca-trust/source/anchors/wolf-root-ca.crt
sudo update-ca-trust
```

#### macOS

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain wolf-ca.crt
```

#### Windows (PowerShell, as Administrator)

```powershell
Import-Certificate -FilePath "wolf-ca.crt" `
  -CertStoreLocation Cert:\LocalMachine\Root
```

#### Browser-specific notes

* **Chrome / Edge** on Windows / macOS use the OS trust store
  installed above — no extra step.
* **Chrome / Edge on Linux** use their own NSS database:

  ```bash
  certutil -d sql:$HOME/.pki/nssdb -A \
    -t "C,," -n "Wolf Root CA" -i wolf-ca.crt
  ```

  (Install `libnss3-tools` if `certutil` isn't on `$PATH`.)
* **Firefox** uses its own trust store on every OS. Open
  `about:preferences#privacy` → **View Certificates…** →
  **Authorities** tab → **Import…** → pick `wolf-ca.crt` → tick
  "Trust this CA to identify websites."

After importing the CA, **fully quit and reopen the browser** —
NSS caches the trust store at startup; a tab reload isn't enough.
Then revisit `https://localhost:3000` (or your LAN IP) and verify
the padlock shows green.

**Verify HTTPS end-to-end from the command line:**

```bash
# Both should return HTTP 200 with the freshly-minted CA trusted.
CA=.local/certs/ca/ca-cert.pem
curl -s --cacert "$CA" -o /dev/null -w "wolf-dashboard: %{http_code}\n" https://localhost:3000/
curl -s --cacert "$CA" -o /dev/null -w "wolf-server: %{http_code}\n" https://localhost:7860/healthz
```

**Verify mTLS is actively enforced** (Phase 5.6-c). Three round-trips
that together prove every layer of the mTLS posture is correct:

```bash
CA=.local/certs/ca/ca-cert.pem
CLIENT_CERT=.local/certs/dashboard-client/cert.pem
CLIENT_KEY=.local/certs/dashboard-client/key.pem

# 1. Direct curl WITHOUT a client cert. wolf-server rejects with 401.
#    Body explains why; not a TLS handshake failure but an
#    application-layer rejection from MtlsMiddleware.
curl -s --cacert "$CA" https://localhost:7860/api/v1/auth/me
# Expected:
#   {"error":"mtls_required","detail":"wolf-server requires a Wolf-CA-signed
#    client certificate..."}

# 2. Same call WITH the dashboard-client cert. mTLS passes; the
#    AuthMiddleware then rejects because there's no login cookie.
#    The "Not authenticated" response means the mTLS hand-off
#    worked correctly.
curl -s --cacert "$CA" --cert "$CLIENT_CERT" --key "$CLIENT_KEY" \
  https://localhost:7860/api/v1/auth/me
# Expected:
#   {"detail":"Not authenticated"}

# 3. /healthz from loopback (no cert) bypasses mTLS for ops tooling.
curl -s --cacert "$CA" https://localhost:7860/healthz
# Expected:
#   {"status":"ok","service":"wolf-server"}
```

If 1 returns anything other than `mtls_required`, wolf-server isn't
enforcing mTLS — check its startup banner shows `mTLS: ENABLED`.
If 2 returns `mtls_cn_rejected` instead of `Not authenticated`, the
client cert's CN doesn't match the allowlist — see the
troubleshooting section below. If 3 returns 401, the /healthz
bypass isn't working — check you're hitting the loopback address
and not the LAN IP.

#### Troubleshooting mTLS

| Symptom | Likely cause | Fix |
|---|---|---|
| Browser shows `NetworkError` after login | dashboard's proxy can't reach wolf-server (wolf-server down or wrong URL) | Check `WOLF_SERVER_URL` env var; `curl https://localhost:7860/healthz` from the dashboard host. |
| Dashboard banner says `proxy mTLS: DISABLED` | The `dashboard-client` cert files are missing | Run `wolf-cert revoke --yes && wolf-cert init` to mint a fresh set; pre-5.6 cert sets didn't include the client leaf. |
| wolf-server banner says `mTLS: DISABLED` | The Wolf CA cert isn't at `.local/certs/ca/ca-cert.pem` | Run `wolf-cert init` (or copy the CA cert into place in a distributed deployment — see §3.13). |
| Direct curl gets `mtls_cn_rejected` with CN `wolf-dashboard-client` | The allowlist on wolf-server has a typo or a stale value | `echo $MTLS_ALLOWED_CLIENT_CNS` (must contain `wolf-dashboard-client`); restart wolf-server. |
| Direct curl gets a bare TLS error, no JSON | uvicorn isn't getting CERT_OPTIONAL; somehow the launcher think mTLS is off | Restart wolf-server; check the banner reports `mTLS: ENABLED`. |
| Dashboard works but `make smoke-mtls` fails | A leftover wolf-server from before `wolf-cert init` is still listening | `pkill -f "python -m wolf_server"; ss -tlnp \| grep :7860` should be empty; then relaunch. |

**Inspect the audit log** to see what wolf-server is doing with
incoming connections. Every accept/reject decision is logged via
structlog with one of these event names:

```bash
# In the journal of wolf-server.log:
grep mtls_ /tmp/wolf-server.log
# Each line includes the cert CN, the rejected reason (if any), and
# the requesting client IP — useful for spotting an misconfigured
# distributed deployment quickly.
```

**To roll back to HTTP + no mTLS:** `wolf-cert revoke --yes`
deletes the cert directory; the next launcher start drops back to
plain HTTP automatically (and mTLS turns off too — they share the
cert-files-are-the-signal contract). You can also remove the CA
from your OS trust store via the inverse of the install commands
above.

### 3.13 Distributed deployment (multi-host)

The all-in-one path above puts every Wolf component on one host,
all reachable via loopback. For real deployments where wolf-server
runs on a different host than wolf-dashboard (e.g. a hardened
"brain" machine on an internal network and an edge "dashboard"
machine behind a corporate proxy), the cert + env story has a few
extra steps.

**Cert distribution:** the operator's admin workstation runs
`wolf-cert init` once. The CA private key (`ca/ca-key.pem`) NEVER
leaves the admin workstation — that's the security posture of a
self-signed CA. The other files get copied:

| File | Goes on | Why |
|---|---|---|
| `ca/ca-cert.pem` | both hosts | trust-chain anchor; needed to validate the other side's cert |
| `server/cert.pem` + `server/key.pem` | wolf-server host only | wolf-server's TLS identity |
| `dashboard/cert.pem` + `dashboard/key.pem` | wolf-dashboard host only | wolf-dashboard's TLS identity (terminates browser HTTPS) |
| `dashboard-client/cert.pem` + `dashboard-client/key.pem` | wolf-dashboard host only | proxy's client identity for outbound mTLS to wolf-server |
| `ca/ca-key.pem` | admin workstation only | NEVER copy this to any service host |

**The one env-var edit:** on the wolf-dashboard host, set
`WOLF_SERVER_URL` to the wolf-server host's URL. The default is
`http://localhost:7860`; in distributed it needs to be e.g.
`https://wolf-server.acme.internal:7860`.

```bash
# In services/dashboard/.env.local on the wolf-dashboard host:
WOLF_SERVER_URL=https://wolf-server.acme.internal:7860
```

That's the only configuration difference between all-in-one and
distributed. Everything else (cert auto-detection, mTLS
enforcement, /healthz bypass) works identically on both
topologies. The browser still only sees `wolf-dashboard`'s
origin; wolf-server is never internet-exposed.

**Future:** `wolf-gateway` (Phase 6) will run on its own host
with a `wolf-gateway-client` leaf cert (parallel to
`dashboard-client`), added to wolf-server's
`MTLS_ALLOWED_CLIENT_CNS` allowlist. Same pattern, one more CN.
The relay daemons (the planned "wolf-relay" component that ships
events from Wazuh hosts to wolf-server) will follow the same
pattern with one `wolf-relay-<organization>` cert per relay.

#### Production distributed deployment via `.deb` packages

The walkthrough above is dev-workflow focused. Production
deployments use the `.deb` packages Wolf publishes (see
[`docs/17-release-engineering.md`](docs/17-release-engineering.md))
and split components across hosts according to the operator's
threat model.

##### Common topologies

**Two-host topology (brain + edge):**

```
                  ┌────────────────────────┐
  Browsers ──────▶│ wolf-dashboard host    │
                  │ (DMZ / corporate VLAN) │
                  │  - wolf-dashboard.deb  │
                  │  - wolf-database.deb*  │
                  └───────────┬────────────┘
                              │ mTLS
                              ▼
                  ┌────────────────────────┐
                  │ wolf-server host       │
                  │ (internal "brain" VLAN)│
                  │  - wolf-server.deb     │
                  │  - wolf-search.deb**   │
                  └───────────┬────────────┘
                              │ Wazuh API
                              ▼
                  ┌────────────────────────┐
                  │ Wazuh manager          │
                  └────────────────────────┘

* wolf-database can live on either host. Most operators
  co-locate it with wolf-server (less network surface).
** wolf-search (SearXNG web research, ADR 0032) is
  wolf-server's sidecar: ALWAYS co-located with wolf-server,
  loopback-only (127.0.0.1:1307) — in every topology. Optional:
  skip it (it's a Recommends) for air-gapped installs or
  hosted search backends.
```

**Three-host topology (brain + edge + DB):**

Same as above but `wolf-database` is on its own host. Used when
the database needs its own backup/HA/network-isolation posture
(e.g., a managed Postgres VM that other services also use).

**Four-host topology (with gateway, post-Phase-6):**

Adds a separate `wolf-gateway` host that runs the execute-tools
and approval-token verification. Operators wanting maximum
blast-radius reduction put the gateway on its own network
segment with its own credentials.

##### Per-host install steps (two-host example)

Run these on the appropriate host. Order matters: install
wolf-database first (it owns the schema), then wolf-server
(applies migrations), then wolf-dashboard (consumes wolf-server).

**On the wolf-server host** (the brain):

```bash
# 1. Add the Wolf APT repository (URL pending; tracked as
#    docs/17 gap 2). Until then, sideload the .deb files.
#    wolf-search is optional-but-default (web research; its
#    postinst needs network to github.com + PyPI):
sudo apt install ./wolf-database_0.1.0_amd64.deb \
                 ./wolf-server_0.1.0_amd64.deb \
                 ./wolf-search_0.1.0_all.deb

# 2. Initialize the cluster (one-shot):
sudo -u wolf-database \
    WOLF_DATABASE_PRODUCTION=1 \
    /usr/bin/wolf-database init

# 3. Copy the printed DATABASE_URL into wolf-server's env:
sudo $EDITOR /etc/wolf-server/env

# Minimum required env vars:
#   DATABASE_URL=postgresql+asyncpg://wolf:<pwd>@localhost:5432/wolf
#   SECRET_KEY=<32+ random chars>
#   SECRETS_BACKEND=file
#   SECRETS_FILE_PATH=/var/lib/wolf-server/secrets.enc
#   SECRETS_FILE_KEY=<cryptography.fernet.Fernet.generate_key()>
#   ENVIRONMENT=production
#   MTLS_ALLOWED_CLIENT_CNS=wolf-dashboard-client

# 4. Copy the certs minted on your admin workstation:
sudo install -d -m 0750 -o root -g wolf /etc/wolf/certs
sudo install -d -m 0750 -o root -g wolf /etc/wolf/certs/ca
sudo install -d -m 0750 -o root -g wolf /etc/wolf/certs/server
sudo cp ca/ca-cert.pem        /etc/wolf/certs/ca/
sudo cp server/cert.pem       /etc/wolf/certs/server/
sudo install -m 0640 -o wolf-server -g wolf \
    server/key.pem            /etc/wolf/certs/server/

# 5. Start the services. Per ADR 0016 v3 they're fully
#    independent; wolf-server has a built-in retry loop for
#    wolf-database not being ready.
sudo systemctl enable --now wolf-database wolf-server

# 6. Verify:
journalctl -u wolf-database -n 20 --no-pager
journalctl -u wolf-server -n 20 --no-pager
curl --cacert /etc/wolf/certs/ca/ca-cert.pem https://localhost:7860/healthz
# Expect: {"status":"ok"}
```

**On the wolf-dashboard host** (the edge):

```bash
# 1. Install wolf-dashboard:
sudo apt install ./wolf-dashboard_0.1.0_amd64.deb

# 2. Provision the env file:
sudo $EDITOR /etc/wolf-dashboard/env
# Minimum:
#   WOLF_SERVER_URL=https://wolf-server.internal:7860
#   (use the brain host's DNS name or IP)

# 3. Copy the certs:
sudo install -d -m 0750 -o root -g wolf /etc/wolf/certs/ca
sudo install -d -m 0750 -o root -g wolf /etc/wolf/certs/dashboard
sudo install -d -m 0750 -o root -g wolf /etc/wolf/certs/dashboard-client
sudo cp ca/ca-cert.pem /etc/wolf/certs/ca/
sudo cp dashboard/cert.pem dashboard/key.pem \
        /etc/wolf/certs/dashboard/
sudo install -m 0640 -o wolf-dashboard -g wolf \
    dashboard/key.pem /etc/wolf/certs/dashboard/
sudo cp dashboard-client/cert.pem dashboard-client/key.pem \
        /etc/wolf/certs/dashboard-client/
sudo install -m 0640 -o wolf-dashboard -g wolf \
    dashboard-client/key.pem /etc/wolf/certs/dashboard-client/

# 4. Start:
sudo systemctl enable --now wolf-dashboard

# 5. Verify:
journalctl -u wolf-dashboard -n 20 --no-pager
curl --cacert /etc/wolf/certs/ca/ca-cert.pem https://localhost:3000/
# Expect: HTML response (the login page).
```

##### Network + firewall rules

For each topology, the operator must permit these inbound
connections:

| To host | Port | From | Reason |
|---|---|---|---|
| wolf-dashboard | 443 (or 3000) | Browser network | The only origin browsers connect to |
| wolf-server | 7860 | wolf-dashboard host | mTLS-protected API |
| wolf-database | 5432 | wolf-server host (if separate) | DB access |
| Wazuh manager | 55000 / 1514 / 1515 | wolf-server host | Wazuh API access |

Every other inter-component port should be blocked at the
firewall level. wolf-server's mTLS middleware enforces this
at the application layer too — but defense-in-depth.

##### Common troubleshooting

- **"mTLS: DISABLED" in wolf-server's journal** — the certs
  aren't at the expected paths under `/etc/wolf/certs/` on
  the wolf-server host. Check ownership: the dirs need
  `0750 root:wolf` and the key file needs `0640 wolf-server:wolf`.
- **Browser shows certificate-trust warning** — operators have
  to add the Wolf CA cert (`ca/ca-cert.pem`) to their
  browser's trust store. There's no public CA chain; that's
  the deliberate posture of a self-signed CA.
- **"connection refused" from wolf-dashboard to wolf-server** —
  firewall isn't permitting the dashboard → server traffic on
  port 7860. Verify with `nc -zv wolf-server.internal 7860`
  from the wolf-dashboard host.
- **wolf-server crashes on startup with "Database connection
  failed"** — DATABASE_URL in `/etc/wolf-server/env` is wrong
  or the database isn't running. wolf-server has a 120s retry
  loop on startup; if it times out, journalctl shows the
  underlying error.

---

## 4. Verifying everything works

Run these in order. If any fails, fix it before moving on.

### 4.1 Unit + integration tests (128 currently passing)

```bash
make test                # full backend suite
make test-isolation      # the cross-organization isolation suite alone
make test-cov            # with coverage report; gates at 80%
```

### 4.2 Lint + typecheck

```bash
make lint                # ruff
make typecheck           # mypy strict on safety-critical packages
make check               # lint + typecheck + test
```

### 4.3 Live smoke against your real Wazuh (only if you wired one in 3.9)

```bash
cd services/server
uv run python -m wolf_server.management.smoke_wazuh --organization-slug acme --all-tools
```

This exercises every registered read tool against the live deployment.
It is the canonical "does Wolf actually talk to Wazuh" check and the
one you re-run after any Wazuh upgrade or tool change.

### 4.4 wolf-dashboard build

```bash
cd services/dashboard
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
# 1. Postgres — system Postgres auto-starts on boot via systemd;
#    no action needed. Verify with:
sudo systemctl status postgresql
# (If you chose the Docker-Postgres alternative path, use:
#    docker compose up -d postgres)

# 2. Ollama
ollama serve &

# 3. wolf-server
cd services/server
set -a && source ../../.env && set +a
uv run python -m wolf_server &       # Phase 5.4-c launcher; auto-HTTPS when certs present
cd ../..

# 4. wolf-dashboard
cd services/dashboard
npm run dev -- --hostname 0.0.0.0 --port 3000 &
cd ..
```

### Add a new organization

Same command as §3.9 — `bootstrap_organization` is the entry point.
Substitute the new organization's values:

```bash
cd services/server
uv run python -m wolf_server.management.bootstrap_organization \
    --organization-slug <slug> --organization-name "<Display Name>" \
    --admin-email <email> --admin-password <password> \
    --opensearch-url <url> --opensearch-username <user> --opensearch-password <pw> \
    --server-api-url <url> --server-api-username <user> --server-api-password <pw> \
    --no-verify-tls
cd ../..
```

### Update an existing organization's Wazuh config

Re-run `bootstrap_organization` with the same `--organization-slug` and the changed
fields (all required flags must still be passed — the CLI does not
accept partial updates). It will:

- Update the organization row's URLs / TLS flag / `inject_organization_filter` flag.
- Re-stash both Wazuh credential blobs in the secrets backend.
- Re-hash and overwrite the admin user's password (if you pass a new one).
- Preserve the user↔organization role binding.

The docstring at the top of
[`bootstrap_organization.py`](services/server/wolf_server/management/bootstrap_organization.py)
documents this contract. A smaller-surface "update only" CLI is a
welcome future ergonomic improvement; today, re-running
`bootstrap_organization` is the supported path.

### Rotate a organization's Wazuh credentials

Re-run `bootstrap_organization` with the same `--organization-slug`, the same URLs,
and the new credentials in `--opensearch-password` / `--server-api-password`.
The secrets backend is overwritten in place; the resolver will pick up
the new values on its next lookup (per-request, no caching).

### Flip the default model

1. Pull the candidate model with Ollama.
2. Run `tools.model_probe` against it.
3. Write an ADR (`docs/decisions/0NNN-...md`) following the ADR 0004 pattern.
4. Change `default_model_id` in [`services/server/wolf_server/config.py`](services/server/wolf_server/config.py).
5. Restart wolf-server. Verify with a chat curl.

Full procedure in [`docs/14-model-recommendations.md`](docs/14-model-recommendations.md) §"Environment-change playbook".

### Use a hosted API instead of Ollama

```bash
# Stash the key once, never share it again
printf 'sk-...' | uv run python -m wolf_server.management.set_secret \
    --key model.openrouter.api_key

# Override the model envs (OpenAI-compatible adapter)
export DEFAULT_MODEL_PROVIDER=openai
export DEFAULT_MODEL_ID=nvidia/nemotron-3-super-120b-a12b:free
export OPENAI_BASE_URL=https://openrouter.ai/api    # NOT .../api/v1 — see Gotcha #2
export DEFAULT_MODEL_API_KEY_REF=model.openrouter.api_key

# Restart wolf-server with this env
```

The full verification pattern is documented in ADR 0005.

### Rotate a secret

`set_secret` overwrites in place. Pipe the new value to it the same way you piped the original.

### Run a one-off Alembic migration

```bash
cd services/server
uv run alembic revision --autogenerate -m "add column foo to organizations"
$EDITOR migrations/versions/<new_file>.py     # review the autogen output
uv run alembic upgrade head
cd ../..
```

---

## 6. Gotchas (real ones that bit us)

### Gotcha #1 — Two `app/` packages collide (RESOLVED 2026-06-03 in Phase 5.5)

This used to bite people because both `services/orchestrator/app/`
and `services/gateway/app/` exposed a top-level Python package
named `app`, and whichever one Python found first on `sys.path` won.
Recurring source of confusion.

**Fixed in Phase 5.5** (the component-renaming refactor — see
CHANGELOG 2026-06-03 + ADR 0016): the packages are now
`services/server/wolf_server/` and `services/gateway/wolf_gateway/`,
two distinct names that cannot collide. The model_probe CLI's
historical sys.path workaround was removed in the same slice; it
now imports `from wolf_server.models.ollama import OllamaAdapter`
directly via uv's editable workspace install.

Kept as a section here for archaeological reference (commit
history will surface this when greppers go looking for `app` /
`orchestrator`).

### Gotcha #2 — `OPENAI_BASE_URL` must NOT include `/v1`

The OpenAI adapter appends `/v1/chat/completions` itself. Setting
`OPENAI_BASE_URL=https://openrouter.ai/api/v1` produces a doubled `/v1`
and a 404. Correct: `https://openrouter.ai/api`. Documented inline on
the OpenRouter `KNOWN_MODELS` entries in
[`services/server/wolf_server/models/interface.py`](services/server/wolf_server/models/interface.py).

### Gotcha #3 — `inject_organization_filter` is opt-in for a reason

A stock Wazuh deployment does not stamp `organization_id` on documents.
If you set `OrganizationWazuhConfig.inject_organization_filter=True` against a
vanilla Wazuh, every read tool returns zero results — Wolf is
filtering correctly, the data just doesn't carry the field. Leave it
`False` (the default) for single-organization / standalone deployments;
turn it on for MSSP deployments where ingestion stamps the field at
indexing time. See [`docs/05-multi-organization.md`](docs/05-multi-organization.md).

### Gotcha #4 — LAN access has mostly become a non-issue

The `a3fdd73` IP-agnostic-dev change (2026-05-31) folded the
three-file LAN-IP rotation paper-cut into one regex: the backend's
`CORS_ALLOW_ORIGIN_REGEX` default and wolf-dashboard's
`allowedDevOrigins` both match any private-network range
(192.168/16, 10/8, 172.16/12) on any port. wolf-server binds
`0.0.0.0` by default (the `BIND_HOST` setting, see `.env.example`).
So in dev, a fresh LAN IP usually requires zero edits.

If you need to lock it down (production deployments, restricted
networks), explicitly set:

1. `BIND_HOST` in `.env` to a specific interface.
2. `CORS_ALLOW_ORIGINS` (the exact-list field) to the URLs you
   accept.
3. `CORS_ALLOW_ORIGIN_REGEX` to `""` to disable the
   any-private-range wildcard.
4. `allowedDevOrigins` in [`services/dashboard/next.config.ts`](services/dashboard/next.config.ts)
   for the dev-server-side check.

If you're running HTTPS via `wolf-cert` (Phase 5.4) and the LAN IP
changes, you also need `wolf-cert add-host <new-ip>` so the new IP
ends up in the leaf cert's SAN list — without it the browser
rejects the connection on hostname mismatch.

### Gotcha #5 — Models occasionally send `{"limit": null}`

Small models sometimes emit explicit-null fields for optional
parameters. The dispatcher strips them
([`services/server/wolf_server/tools/dispatcher.py`](services/server/wolf_server/tools/dispatcher.py),
`strip_explicit_nulls`). If you add a new tool with optional fields,
this protection is already in place — don't disable it.

### Gotcha #6 — Relative-time strings on alert tools

Some models pass `time_from="now-24h"` instead of an ISO timestamp.
[`services/server/wolf_server/tools/alerts.py`](services/server/wolf_server/tools/alerts.py)
has a Pydantic `field_validator` to parse this. If you add a tool that
accepts time inputs, copy the validator pattern.

### Gotcha #7 — Wazuh Indexer and Server API have separate user backends

The Wazuh Indexer (OpenSearch security plugin) and the Wazuh Server API
(its own RBAC database at `/var/ossec/api/configuration/security/rbac.db`)
maintain **two separate user databases**. A user that authenticates to
the Indexer may not exist on the Server API — and vice versa.

Typical example from the Wazuh OVA install: `admin` works for the
Indexer; `wazuh-wui` (or a generated password) is the Server API admin.

Phase 4 Slice 2's `bootstrap_organization` now probes BOTH endpoints with the
supplied credentials before persisting the organization. The error message on
a Server-API 401 explicitly names this gotcha — but knowing it ahead of
time saves a debugging session. When in doubt, run `sudo
/var/ossec/bin/wazuh-passwords-tool.sh -a -A` on the Wazuh host to dump
the actual Server-API credentials.

### Gotcha #8 — Phase 4 multi-organization: two organizations are required for full coverage

The cross-organization isolation suite (`tools/organization_isolation_test`) needs
TWO organizations bootstrapped to be meaningful — a single-organization deployment
has nothing to leak against. The dev pattern:

```bash
# 1. Bootstrap acme (the primary dev organization)
uv run python -m wolf_server.management.bootstrap_organization --organization-slug acme \
    --organization-name "Acme SecOps" ... --no-verify-tls

# 2. Bootstrap beta against the SAME Wazuh (bridge model — application-
#    layer isolation is what we test, not Wazuh-instance separation)
uv run python -m wolf_server.management.bootstrap_organization --organization-slug beta \
    --organization-name "Beta InfoSec" ... --no-verify-tls

# 3. Seed each organization's private corpus
uv run python -m wolf_server.management.seed_dev_knowledge --organization-slug acme
uv run python -m wolf_server.management.seed_dev_knowledge --organization-slug beta

# 4. Run the live isolation suite
make test-isolation-live
```

Per ADR-pending `docs/05-multi-organization.md` §Test isolation as a
first-class continuous practice, run this suite **constantly** — in CI
on every PR (covered by `make test-isolation`) AND as a periodic probe
against the dev / staging / production DB (the `test-isolation-live`
target).

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
passing. mypy strict clean on 33 safety-critical files. wolf-dashboard
(Next.js 16; package was `frontend/` at this snapshot date) renders
chat, citations, multi-turn, organization switcher.

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
| App entrypoint (FastAPI) | [`services/server/wolf_server/main.py`](services/server/wolf_server/main.py) |
| Config / env settings | [`services/server/wolf_server/config.py`](services/server/wolf_server/config.py) |
| Agent loop (strategies) | [`services/server/wolf_server/agent/`](services/server/wolf_server/agent/) |
| Model adapters + KNOWN_MODELS | [`services/server/wolf_server/models/`](services/server/wolf_server/models/) |
| Tool definitions + dispatcher | [`services/server/wolf_server/tools/`](services/server/wolf_server/tools/) |
| Wazuh clients (Indexer + API) | [`services/server/wolf_server/wazuh/`](services/server/wolf_server/wazuh/) |
| Tenancy + auth | [`services/server/wolf_server/tenancy/`](services/server/wolf_server/tenancy/), [`services/server/wolf_server/auth/`](services/server/wolf_server/auth/) |
| Audit log | [`services/server/wolf_server/audit/`](services/server/wolf_server/audit/) |
| Guardrails | [`services/server/wolf_server/guardrails/`](services/server/wolf_server/guardrails/) |
| Management CLIs | [`services/server/wolf_server/management/`](services/server/wolf_server/management/) |
| Alembic migrations | [`services/server/migrations/versions/`](services/server/migrations/versions/) |
| Backend tests | [`services/server/tests/`](services/server/tests/) |
| Shared schema types | [`packages/schema/wolf_schema/`](packages/schema/wolf_schema/) |
| Secrets backend | [`packages/secrets/wolf_secrets/`](packages/secrets/wolf_secrets/) |
| Logging / tracing helpers | [`packages/common/wolf_common/`](packages/common/wolf_common/) |
| wolf-dashboard app | [`services/dashboard/`](services/dashboard/) |
| wolf-dashboard chat shell | [`services/dashboard/components/chat-shell.tsx`](services/dashboard/components/chat-shell.tsx) |
| wolf-dashboard stream hook | [`services/dashboard/hooks/use-conversation-streams.ts`](services/dashboard/hooks/use-conversation-streams.ts) |
| wolf-dashboard Next config (CORS / origins) | [`services/dashboard/next.config.ts`](services/dashboard/next.config.ts) |
| Capability probe CLI | [`tools/model_probe/`](tools/model_probe/) |
| Compose (Postgres in dev) | [`docker-compose.yml`](docker-compose.yml), [`docker-compose.dev.yml`](docker-compose.dev.yml) |
| Makefile (test / lint / typecheck / probe targets) | [`Makefile`](Makefile) |
| CI | [`.github/workflows/ci.yml`](.github/workflows/ci.yml) |

---

## 10. When something doesn't work

In rough order of "what to try first":

| Symptom | Likely cause | Where to look |
|---|---|---|
| `/api/v1/auth/login` returns 404 | uvicorn picked up gateway's `app/` | Gotcha #1; `cd services/server` first |
| Chat returns "no tools called" or empty answer | Model entry in `KNOWN_MODELS` says `recommended_strategy='pipeline'` for a model that can actually do native tool calls | Re-probe; amend entry; see commit `14cc727` for the pattern |
| Read tools return 0 results | `inject_organization_filter=True` on a vanilla Wazuh | Gotcha #3; flip to False |
| Hosted API returns 404 | `OPENAI_BASE_URL` includes `/v1` | Gotcha #2 |
| LAN browser can't load wolf-dashboard | One of three things misconfigured | Gotcha #4 |
| `loop_error` mid-conversation | Model adapter raised; check the audit table for the captured exception type + traceback (commit `e09b4e5`) | `services/server/wolf_server/agent/loop.py` |
| Tests fail on a clean checkout | First check Postgres is up and migrations are applied | `make test` after `docker compose up -d postgres` + `make migrate-local` |
| mypy complains about a new file | The strict gate covers the safety-critical packages listed in the [`Makefile`](Makefile) `typecheck` target | Add explicit types or move it outside the gated set with justification |

If none of the above match, the audit log table (`audit_log` in
Postgres) records every model call and tool call with arguments and
results. Read the last few rows for the failing organization — the answer
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
