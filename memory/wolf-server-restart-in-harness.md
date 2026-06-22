---
name: wolf-server-restart-in-harness
description: "How to restart wolf-server from the Bash tool without killing the harness shell (pkill -f self-match) + how to verify it's up"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

Restarting **wolf-server** (listens on **:7860**, launched `uv run python -m wolf_server` from `services/server` with the repo-root `.env` sourced) from the Claude Code Bash tool:

- **Do NOT `pkill -f "python -m wolf_server"`** — that pattern appears in the harness's own `bash -c "…"` argv, so `pkill -f` kills the **parent shell** (command exits **144**) and may leave the server alive. Seen repeatedly.
- **Stop by explicit PID instead:** `PID=$(ss -ltnp | grep ':7860 ' | grep -oP 'pid=\K[0-9]+' | head -1); kill "$PID"; timeout 25 tail --pid="$PID" -f /dev/null` (waits for the port to free). `tail --pid` is the no-`sleep` wait (foreground `sleep` is blocked by the tool).
- **Relaunch in background** (`run_in_background: true`) with absolute paths to avoid cwd issues: `set -a && source /home/alsechemist/Codespace/project-wolf/.env && set +a && uv run --directory /home/alsechemist/Codespace/project-wolf/services/server python -m wolf_server`. The `nohup … & disown` form ALSO fails under the tool (exit 144) — use `run_in_background` instead.
- **Verify:** wolf-server serves **HTTPS** on 7860 when `.local/certs/server/{cert,key}.pem` exist → `curl -k --retry 60 --retry-connrefused https://localhost:7860/api/v1/auth/login` (a plain HTTP curl returns `000`). **HTTP 401 means it's UP** (the runbook's dev cred `admin@example.com`/`wolf_admin_dev_password` may not exist in this DB — that's fine for a liveness check).

Canonical runbook is `docs/restart.md`, but it uses `pkill -f` (the self-match caveat above applies in this harness). Restart is part of the per-slice web-test handoff — the long-running service holds **pre-edit code** until restarted (the 6-b.1/6-c.1 "stale process" lesson; verify the new process's start time is after your edits). Related: [[per-slice-web-test-checkpoints]], [[next-dev-cache-vs-build]].
