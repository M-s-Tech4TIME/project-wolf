# Restart Wolf — fresh state runbook

The exact procedure to bring Wolf back to a known-clean state before a
manual web-test, after backend changes, or any time things feel odd.
Tuned for the per-slice workflow (see the cross-session memory entry
`per-slice-web-test-checkpoints.md`).

This file is the operational reference so it does not have to be
re-derived from scratch every test cycle.

---

## Quick version (you know what you're doing)

```bash
# 1. Stop wolf-server + unload models
pkill -f "uvicorn wolf_server.main:app" 2>/dev/null   # legacy invocation
pkill -f "python -m wolf_server" 2>/dev/null          # Phase 5.4-c launcher
ollama stop qwen3:4b 2>/dev/null
ollama stop qwen3:8b 2>/dev/null

# 2. Confirm GPU + port are clean
ollama ps
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
ss -tlnp 2>/dev/null | grep ":7860 " || echo "port 7860 free"

# 3. Relaunch wolf-server via the Phase 5.4-c launcher.
#    `python -m wolf_server` auto-detects TLS: HTTPS when both
#    .local/certs/server/{cert,key}.pem exist, HTTP otherwise.
#    The first log line tells you which scheme the launcher picked.
cd services/server
set -a && source ../../.env && set +a
nohup uv run python -m wolf_server \
  > /tmp/wolf-server.log 2>&1 & disown

# 4. Verify with a login round-trip.
#    If wolf-server is running HTTPS (post-`wolf-cert init`),
#    swap `http` → `https` AND add `--insecure` (self-signed CA isn't
#    in curl's trust store; for browsers we install it via
#    `wolf-cert export-ca` per ONBOARDING.md).
curl -s --retry 40 --retry-delay 1 --retry-connrefused --max-time 60 \
  -o /dev/null -w "login HTTP %{http_code}\n" \
  -X POST http://localhost:7860/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"wolf_admin_dev_password"}'
# expect: login HTTP 200
```

If `login HTTP 200`, Wolf is live. Open `http://<lan-ip>:3000` (or
`https://<lan-ip>:3000` when `.local/certs/dashboard/{cert,key}.pem`
exist — the Phase 5.4-d launcher auto-enables TLS) in the browser.
wolf-dashboard hot-reloads on its own; no Next.js restart needed
unless `next.config.ts` changed.

---

## Why each step

### Stop wolf-server

`pkill -f "uvicorn wolf_server.main:app"` (legacy) AND `pkill -f "python -m wolf_server"`
(Phase 5.4-c launcher) — kills the running wolf-server process by
matching the exact command string. Both patterns are listed because
the work-in-progress will move from the first to the second; either
pattern is safe to run when the corresponding process is absent.
wolf-server runs without `--reload`, so Python edits don't pick
up until a manual restart.

### Unload Ollama models

`ollama stop qwen3:4b` and `ollama stop qwen3:8b` — unloads the chat
and grounding-judge models from GPU memory. Two reasons:

1. A fresh GPU avoids fragmentation. The judge (qwen3:8b) doesn't fit
   alongside the chat (qwen3:4b) on this 6 GB GPU, so Ollama already
   swaps them per call — starting clean prevents leftover-model
   weirdness.
2. The user's standing per-slice workflow expects a known-empty GPU
   before each test cycle.

A stuck model that says `Stopping...` for more than ~10 s can be
re-targeted with another `ollama stop <model>` — Ollama's daemon
finishes the shutdown.

### Verify clean state

`ollama ps` should print only the header (no rows). `nvidia-smi
memory.used` should be ~15 MiB (essentially idle). `ss -tlnp | grep
:7860` should find nothing.

If GPU memory is still high after `ollama stop`, run `ollama stop`
again for any model that appears in `ollama ps`. If port 7860 is still
bound, find the PID with `lsof -i :7860` (or `ss -tlnp | grep :7860`)
and `kill <pid>`.

### Relaunch wolf-server

`set -a && source ../../.env && set +a` exports all variables in `.env`
into the shell. The launcher (`wolf_server/__main__.py`) then reads
`DATABASE_URL`, `SECRET_KEY`, secrets-backend paths, the Ollama base
URL, the grounding judge model ID, the embedding model env vars, AND
the new `BIND_HOST` / `BIND_PORT` / `TLS_CERT_PATH` / `TLS_KEY_PATH`
fields — everything `wolf_server.config.Settings` expects.

`python -m wolf_server` is the Phase 5.4-c launcher. It calls `uvicorn.run`
under the hood, but ALSO inspects `TLS_CERT_PATH` and `TLS_KEY_PATH`
at startup: when both files exist wolf-server serves HTTPS,
otherwise it falls back to plain HTTP. The first line of
`/tmp/wolf-server.log` reports which scheme was picked and why.

`nohup … & disown` detaches the process from the shell so closing the
terminal does not kill the server. Stdout/stderr go to
`/tmp/wolf-server.log`.

### Verify login

The login round-trip exercises the FastAPI startup, the database, the
secrets backend, the password hash, the JWT issuer, and the audit
writer in one HTTP call. If it returns `200`, every load-bearing
subsystem is healthy. If it returns anything else, read
`/tmp/wolf-server.log` — that's the source of truth.

### Verify mTLS came up (Phase 5.6-c)

If you have `.local/certs/` on disk (i.e. you've run `wolf-cert init`),
wolf-server's startup banner should say `mTLS: ENABLED`:

```bash
grep "mTLS:" /tmp/wolf-server.log
# Expected:  mTLS: ENABLED — Wolf CA at ...; allowed client CNs: [wolf-dashboard-client]
```

A three-line smoke that confirms mTLS is actively enforcing
(not just configured):

```bash
CA=.local/certs/ca/ca-cert.pem
CLIENT_CERT=.local/certs/dashboard-client/cert.pem
CLIENT_KEY=.local/certs/dashboard-client/key.pem

# 1. No client cert → 401 mtls_required (mTLS rejected at app layer)
curl -s --cacert "$CA" -w "\n" https://localhost:7860/api/v1/auth/me

# 2. With dashboard-client cert → 401 Not authenticated (mTLS passed,
#    auth-middleware then rejected because no login cookie — this is
#    the correct hand-off between the two middlewares)
curl -s --cacert "$CA" --cert "$CLIENT_CERT" --key "$CLIENT_KEY" -w "\n" \
  https://localhost:7860/api/v1/auth/me

# 3. /healthz from loopback (no cert) → 200 (the bypass is working)
curl -s --cacert "$CA" -w "\n" https://localhost:7860/healthz
```

If wolf-server is on plain HTTP (no `wolf-cert init` yet), all three
calls fail with TLS errors — that's expected, the mTLS smoke only
applies to the HTTPS+mTLS path.

---

## What the restart does NOT touch

These keep running and rarely need a reset:

| Service | Port | Notes |
|---|---|---|
| PostgreSQL | `5432` | System-managed. Restart only if the DB itself misbehaves. |
| Ollama daemon | `11434` | The daemon keeps running; we only unload its models. Restart the daemon (`systemctl restart ollama`) only if it stops responding. |
| Next.js dev server | `3000` | Hot-reloads file changes. Restart only after `next.config.ts` changes or if compilation gets stuck. To restart: `pkill -f "next dev"` (covers both `npm run dev` and `npm run dev:plain`) then `cd services/dashboard && npm run dev`. Phase 5.4-d: `npm run dev` invokes `scripts/dev.mjs`, which serves HTTPS when both `.local/certs/dashboard/{cert,key}.pem` exist and HTTP otherwise — the first stdout line reports the scheme. `npm run dev:plain` forces plain HTTP regardless of cert state if you need the old behaviour. |
| Wazuh deployment | `192.168.245.128:9200` / `:55000` | Separate machine. Wolf never restarts it. |

---

## Test credentials

Two pre-seeded tenant admins (dev only — see `docs/CHANGELOG.md` for
provenance):

| Tenant | Email | Password |
|---|---|---|
| Acme SecOps | `admin@example.com` | `wolf_admin_dev_password` |
| Beta InfoSec | `beta-admin@example.com` | `beta_admin_dev_password` |

wolf-dashboard URL: `http://<this-machine's-LAN-IP>:3000` (LAN-accessible)
or `http://localhost:3000` (this machine only). Discover the current
LAN IP with:

```bash
ip -4 -o addr show | awk '$2 != "lo" {print $2": "$4}'
# or:
hostname -I
```

If the LAN IP just changed, three files pin it and must be updated:

| File | What to update |
|---|---|
| `.env` | `CORS_ALLOW_ORIGINS=` — append `http://<new-ip>:3000` |
| `services/dashboard/.env.local` | `NEXT_PUBLIC_SERVER_URL=http://<new-ip>:7860` |
| `services/dashboard/next.config.ts` | `allowedDevOrigins: […]` — append `"<new-ip>"` |

After editing, restart **both** wolf-server and `next dev` (next-dev
captures `NEXT_PUBLIC_*` at build time and `allowedDevOrigins` at
startup).

---

## Hardware fact to remember

GPU is 6 GB (5.64 GiB usable). `qwen3:4b` is ~3.5 GB and fits with
headroom; `qwen3:8b` is ~5 GB and does NOT coexist with 4b. Every
grounding call swaps them. First answer after a fresh start is slow
(2–14 min depending on complexity) because the model is cold-loading
off disk. Steady-state is much faster. See **ADR 0015** for the
trade-off rationale.

---

## When manual web-testing

The full per-slice cycle (referenced from
`per-slice-web-test-checkpoints.md` in cross-session memory):

1. Implement the change (unit tests + lint + mypy + tsc/eslint clean).
2. **Reset to a fresh state** using the Quick version above.
3. **Claude self-validates** by hitting `/api/v1/auth/login` then
   `/api/v1/chat` with representative prompts; tails `/tmp/wolf-server.log`
   for errors.
4. Reset again so the user starts on a clean GPU.
5. Hand over with exact prompts + expected outcomes + honest caveats.
6. User manually verifies in the browser.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `login HTTP 502` / connection refused | wolf-server hasn't finished starting | Wait 5 s and retry (the `--retry` flags in the curl command above handle this for the first 60 s). |
| `login HTTP 401` | DB user gone, password reset, or wrong creds | Verify `admin@example.com` exists in `users`; re-run `bootstrap_tenant` if needed. |
| `login HTTP 500` | Backend exception | `tail -50 /tmp/wolf-server.log` — usually a DB or secrets-backend misconfiguration. |
| `ollama ps` shows a model stuck `Stopping…` | Daemon mid-shutdown | Wait or `ollama stop <model>` again; if persistent, `systemctl restart ollama`. |
| Port 7860 already bound after `pkill` | A child process survived | `lsof -i :7860` to find PID, `kill <pid>`. |
| wolf-dashboard won't refresh | `next dev` got stuck or `next.config.ts` changed | `pkill -f "next dev"` then `cd services/dashboard && npm run dev`. |
| Chat takes > 10 min | qwen3:8b cold load on a fragmented GPU | Normal on first call after a reset. Subsequent calls in the same session are faster. |
