---
name: next-dev-cache-vs-build
description: "Never `rm -rf .next` while wolf-dashboard.service (next dev / Turbopack) is running — it corrupts the live dev cache; restart the dashboard to recover"
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

**Why:** the running dev server holds references into `.next/dev`; wiping the
directory underneath it corrupts its cache mid-flight.

**How to apply:** to force a clean build, prefer `rm -rf .next/standalone` /
the build subdirs, or stop the dev service first. If `.next` was already wiped
and the dev proxy now 000s, the fix is `systemctl --user restart
wolf-dashboard.service` — it rebuilds the dev cache fresh (recovers in ~2s +
first-request recompile). Confirm with an unauth probe through the proxy:
`curl -sk https://localhost:3000/api/v1/auth/me` → 401. Related:
[[per-slice-web-test-checkpoints]].
