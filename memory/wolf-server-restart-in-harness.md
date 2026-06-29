---
name: wolf-server-restart-in-harness
description: "Restart wolf-server/wolf-dashboard in THIS env: they're USER systemd units — use systemctl --user, NOT manual `uv run` (which squats :7860 and crash-loops the unit). Plus the manual fallback + liveness check."
metadata: 
  node_type: memory
  type: reference
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

**PREFERRED (discovered 2026-06-29): this dev box runs wolf-server AND wolf-dashboard as USER systemd units.** Restart them with systemd, not by hand:
- `systemctl --user restart wolf-server.service` (the agent loop / API, **:7860**)
- `systemctl --user restart wolf-dashboard.service` (Next **dev** on **:3000**)
- Check: `systemctl --user is-active wolf-server.service wolf-dashboard.service`; logs via `journalctl --user -u wolf-server.service -n 20 --no-pager`.

**DO NOT launch wolf-server manually with `uv run … python -m wolf_server`.** The unit has a `Restart=` policy, so a hand-launched process **squats :7860** and the systemd unit gets stuck **`activating (auto-restart)`** crash-looping behind it → two contending servers → slow / weird responses. (Cost me a whole confusing debugging detour on 2026-06-29; the manual launches from this session were the squatter.) If you find a manual squatter: `systemctl --user stop wolf-server.service` → kill the `:7860` PID (below) → `systemctl --user start wolf-server.service`.

**`pkill -f` caveat:** never `pkill -f "python -m wolf_server"` — that pattern appears in the harness's own `bash -c "…"` argv, so `pkill -f` kills the **parent shell** (exit **144/143**) and may leave the server alive. Kill by explicit PID instead: `PID=$(ss -ltnp | grep ':7860 ' | grep -oP 'pid=\K[0-9]+' | head -1); kill "$PID"; timeout 25 tail --pid="$PID" -f /dev/null` (`tail --pid` is the no-`sleep` wait — foreground `sleep` is blocked by the tool).

**Manual fallback (ONLY if no systemd unit exists):** `run_in_background: true` with `set -a && source /home/alsechemist/Codespace/project-wolf/.env && set +a && uv run --directory /home/alsechemist/Codespace/project-wolf/services/server python -m wolf_server` (absolute paths; `nohup … & disown` also fails under the tool with exit 144).

**Verify:** wolf-server serves **HTTPS** on 7860 (certs at `.local/certs/server/{cert,key}.pem`) → `curl -k --retry 60 --retry-connrefused https://localhost:7860/api/v1/auth/login` (plain-HTTP curl returns `000`). **HTTP 401 = UP.** Dashboard `:3000` may return `000` briefly while **Next dev recompiles** after a restart — wait/retry, then it serves 200s (don't `rm -rf .next` / `npm run build` while the dev service runs — see [[next-dev-cache-vs-build]]).

Canonical runbook `docs/restart.md` uses `pkill -f` (self-match caveat applies). Restart is part of the per-slice web-test handoff — the long-running service holds **pre-edit code** until restarted (6-b.1/6-c.1 "stale process" lesson; ensure the new process started after your edits + committed code). Related: [[per-slice-web-test-checkpoints]], [[next-dev-cache-vs-build]].
