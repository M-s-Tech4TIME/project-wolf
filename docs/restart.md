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
# 1. Stop orchestrator + unload models
pkill -f "uvicorn app.main:app" 2>/dev/null
ollama stop qwen3:4b 2>/dev/null
ollama stop qwen3:8b 2>/dev/null

# 2. Confirm GPU + port are clean
ollama ps
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
ss -tlnp 2>/dev/null | grep ":8000 " || echo "port 8000 free"

# 3. Relaunch orchestrator (env from .env, detached, log to /tmp)
cd services/orchestrator
set -a && source ../../.env && set +a
nohup uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  > /tmp/orchestrator.log 2>&1 & disown

# 4. Verify with a login round-trip
curl -s --retry 40 --retry-delay 1 --retry-connrefused --max-time 60 \
  -o /dev/null -w "login HTTP %{http_code}\n" \
  -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"wolf_admin_dev_password"}'
# expect: login HTTP 200
```

If `login HTTP 200`, Wolf is live. Open `http://192.168.68.108:3000` in
the browser. Frontend hot-reloads on its own; no Next.js restart needed
unless `next.config.ts` changed.

---

## Why each step

### Stop orchestrator

`pkill -f "uvicorn app.main:app"` — kills the running uvicorn worker by
matching the exact command string. The orchestrator runs without
`--reload`, so Python edits don't pick up until a manual restart.

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
:8000` should find nothing.

If GPU memory is still high after `ollama stop`, run `ollama stop`
again for any model that appears in `ollama ps`. If port 8000 is still
bound, find the PID with `lsof -i :8000` (or `ss -tlnp | grep :8000`)
and `kill <pid>`.

### Relaunch orchestrator

`set -a && source ../../.env && set +a` exports all variables in `.env`
into the shell. `uvicorn` then sees `DATABASE_URL`, `SECRET_KEY`,
secrets-backend paths, the Ollama base URL, the grounding judge model
ID, and the embedding model env vars — everything `app.config.Settings`
expects.

`nohup … & disown` detaches the process from the shell so closing the
terminal does not kill the server. Stdout/stderr go to
`/tmp/orchestrator.log`.

### Verify login

The login round-trip exercises the FastAPI startup, the database, the
secrets backend, the password hash, the JWT issuer, and the audit
writer in one HTTP call. If it returns `200`, every load-bearing
subsystem is healthy. If it returns anything else, read
`/tmp/orchestrator.log` — that's the source of truth.

---

## What the restart does NOT touch

These keep running and rarely need a reset:

| Service | Port | Notes |
|---|---|---|
| PostgreSQL | `5432` | System-managed. Restart only if the DB itself misbehaves. |
| Ollama daemon | `11434` | The daemon keeps running; we only unload its models. Restart the daemon (`systemctl restart ollama`) only if it stops responding. |
| Next.js dev server | `3000` | Hot-reloads file changes. Restart only after `next.config.ts` changes or if compilation gets stuck. To restart: `pkill -f "next dev"` then `cd frontend && npm run dev` (or whatever script `package.json` defines). |
| Wazuh deployment | `192.168.245.128:9200` / `:55000` | Separate machine. Wolf never restarts it. |

---

## Test credentials

Two pre-seeded tenant admins (dev only — see `docs/CHANGELOG.md` for
provenance):

| Tenant | Email | Password |
|---|---|---|
| Acme SecOps | `admin@example.com` | `wolf_admin_dev_password` |
| Beta InfoSec | `beta-admin@example.com` | `beta_admin_dev_password` |

Frontend URL: `http://<this-machine's-LAN-IP>:3000` (LAN-accessible)
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
| `frontend/.env.local` | `NEXT_PUBLIC_ORCHESTRATOR_URL=http://<new-ip>:8000` |
| `frontend/next.config.ts` | `allowedDevOrigins: […]` — append `"<new-ip>"` |

After editing, restart **both** orchestrator and `next dev` (next-dev
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
   `/api/v1/chat` with representative prompts; tails `/tmp/orchestrator.log`
   for errors.
4. Reset again so the user starts on a clean GPU.
5. Hand over with exact prompts + expected outcomes + honest caveats.
6. User manually verifies in the browser.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `login HTTP 502` / connection refused | Orchestrator hasn't finished starting | Wait 5 s and retry (the `--retry` flags in the curl command above handle this for the first 60 s). |
| `login HTTP 401` | DB user gone, password reset, or wrong creds | Verify `admin@example.com` exists in `users`; re-run `bootstrap_tenant` if needed. |
| `login HTTP 500` | Backend exception | `tail -50 /tmp/orchestrator.log` — usually a DB or secrets-backend misconfiguration. |
| `ollama ps` shows a model stuck `Stopping…` | Daemon mid-shutdown | Wait or `ollama stop <model>` again; if persistent, `systemctl restart ollama`. |
| Port 8000 already bound after `pkill` | A child process survived | `lsof -i :8000` to find PID, `kill <pid>`. |
| Frontend won't refresh | `next dev` got stuck or `next.config.ts` changed | `pkill -f "next dev"` then `cd frontend && npm run dev`. |
| Chat takes > 10 min | qwen3:8b cold load on a fragmented GPU | Normal on first call after a reset. Subsequent calls in the same session are faster. |
