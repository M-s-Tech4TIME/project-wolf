---
name: next-dev-cache-vs-build
description: "Don't `rm -rf .next` OR run `npm run build` while wolf-dashboard.service (next dev / Turbopack) is running — both disturb the live dev cache (proxy 000s). And after a prod build, next dev restarted on top serves a STALE client bundle — clear .next while STOPPED before restarting dev"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

The dev dashboard runs as a persistent service `wolf-dashboard.service`
(`npm run dev` → `node scripts/dev.mjs` → `next dev` with Turbopack, HTTPS on
:3000 via the dashboard cert). Turbopack keeps a live on-disk cache at
`services/dashboard/.next/dev/cache/turbopack/`.

**Gotcha (hit 2026-06-14 during 6.5-d):** running `rm -rf .next` to force a
clean production `npm run build` while the dev service is still running deletes
the SST files the live Turbopack process still references. Every subsequent
request to `/api/[...path]` then panics: "Failed to open SST file
.next/dev/cache/turbopack/.../00000001.sst — No such file or directory", and
curl to the proxy hangs → HTTP 000 (the TLS handshake completes, but no HTTP
response comes).

**Also hit 2026-06-14 during 6.5-e:** running `npm run build` (production
`next build`) while the dev service is up ALSO disturbs the live dev server —
the proxy went to HTTP 000 even with NO `rm`. So the hazard isn't just deleting
`.next`; a concurrent build writing into `.next` is enough.

**Why:** the running dev server holds references into `.next`; deleting it OR a
concurrent `next build` writing into it corrupts/invalidates its cache
mid-flight.

**Also hit 2026-06-16 during 6.5-h — the STALE-BUNDLE trap (worst one):**
after `npm run build` writes production artifacts into `.next/`, restarting
`wolf-dashboard.service` (`next dev`) ON TOP of that prod build makes next dev
serve a STALE client bundle — the OLD compiled JS, not your current source. It
looks like your latest frontend change "didn't take" (e.g. a login-routing fix
that still routes the old way). The page loads fine (no 000), it's just running
old code. **Fix:** stop the service, `rm -rf .next` (safe while STOPPED), then
start the service — next dev recompiles from current source. Verify the served
client chunk actually contains your change:
`grep -rl "<your new symbol>" services/dashboard/.next/dev/static/chunks/`.

**Also hit 2026-06-16 (6.5-h.2 follow-up) — the STALE TS-SERVER `.next/types`
trap:** `next build` generates route/type files under **`.next/types/`** (build
location), while `next dev` generates them under **`.next/dev/types/`** (Next 16
dev location); the dashboard `tsconfig.json` `include` lists BOTH. So a *local*
`npm run build` creates `.next/types/{routes.d.ts,validator.ts,cache-life.d.ts}`,
the editor's TS-server enumerates them into its program, and then the
`rm -rf .next` clear deletes them → VSCode's PROBLEMS tab shows "File
'.next/types/…' not found … matched by include pattern" (3 stale diagnostics).
It is NOT a code/config bug — `tsc --noEmit` is clean (the glob harmlessly
matches nothing in pure dev, where only `.next/dev/types/` exists). **Fix:** the
on-disk state self-heals (dev only writes `.next/dev/types/`); the operator just
needs a one-time **"TypeScript: Restart TS Server"** (or reload window) to drop
the stale in-memory program. **Prevent it:** prefer NOT to run `next build` in
the live workspace — `tsc --noEmit` + `eslint` validate locally and CI's
`frontend` job runs the real build. If you must build locally, after the
`rm -rf .next` clear also delete the (gitignored) `tsconfig.tsbuildinfo`.

**How to apply:** the frontend gate's `tsc --noEmit` + `eslint` are safe to run
against the live dev server (they don't write `.next`, and it hot-reloads
edits, so the UI is validated live anyway) — prefer these over a local
`npm run build` (CI runs the full build). If you DO run the production
`npm run build`, do it with the service STOPPED, and if the operator still needs
the dev server afterward (e.g. a web-test), clear `.next` while stopped before
restarting `next dev` — otherwise it serves the stale prod bundle (and reload
the editor's TS server to clear the `.next/types` diagnostics above). If the dev
proxy returns 000, restart the dashboard — it rebuilds the dev cache fresh (~2s
+ first-request recompile). Confirm with an unauth probe:
`curl -sk https://localhost:3000/api/v1/auth/me` → 401. (A 502 instead means
the dashboard is up but wolf-server is down — restart `wolf-server.service`.)
Related: [[per-slice-web-test-checkpoints]], [[ci-audit-before-push]].
