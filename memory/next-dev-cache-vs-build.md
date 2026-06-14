---
name: next-dev-cache-vs-build
description: "Don't `rm -rf .next` OR run `npm run build` while wolf-dashboard.service (next dev / Turbopack) is running — both disturb the live dev cache (proxy 000s); restart the dashboard to recover"
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

**How to apply:** the frontend gate's `tsc --noEmit` + `eslint` are safe to run
against the live dev server (and it hot-reloads edits, so the UI is validated
live anyway). For the production `npm run build`, stop `wolf-dashboard.service`
first, or just expect to `systemctl --user restart wolf-dashboard.service`
afterward. If the dev proxy returns 000, restart the dashboard — it rebuilds the
dev cache fresh (~2s + first-request recompile). Confirm with an unauth probe:
`curl -sk https://localhost:3000/api/v1/auth/me` → 401. (A 502 instead means
the dashboard is up but wolf-server is down — restart `wolf-server.service`.)
Related: [[per-slice-web-test-checkpoints]].
