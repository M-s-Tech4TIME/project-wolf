---
name: same-network-gate-deferred
description: "6.5-h.2 (future): ADR 0018's same-network verification gate was SPLIT out of 6.5-h because a robust gate needs the browser's true IP, which only the dashboard tier sees — Next 16 exposes no socket to route handlers and its x-forwarded-for is client-spoofable. Needs a custom dashboard server."
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

PLAN (captured 2026-06-16, deferred from 6.5-h per operator decision): ADR 0018
item 9 pairs invite-link verification with a **same-network gate** — verify only
from inside Wolf's network. The verification flow shipped in 6.5-h; the gate is
its own future slice **6.5-h.2**.

**Why it can't be a one-line check (the core finding):** the browser only ever
talks to **wolf-dashboard** (single-origin model, ADR 0016); the dashboard proxy
(`services/dashboard/app/api/[...path]/route.ts`) forwards to wolf-server via
undici, so wolf-server sees the *dashboard's* connection IP (loopback when
co-located), NOT the browser's. The browser's real IP is only observable at the
dashboard tier (the only thing that terminates the browser TCP connection). And:

- Next 16 `NextRequest` exposes **no socket / no `.ip`** to route handlers
  (verified against the installed types).
- Next's node server does `req.headers['x-forwarded-for'] ??= socket.remoteAddress`
  (`node_modules/next/dist/server/base-server.js`), but `??=` **preserves a
  client-supplied XFF** — so a script with a stolen cookie+token can just send
  `X-Forwarded-For: <a private IP>` and walk through. `X-Forwarded-For` is not a
  forbidden fetch header. So reading XFF is spoofable → security theater.

**What 6.5-h.2 must build:** a **custom dashboard Node server** (replacing
`next dev`/`next start`) that, before handing the request to Next, STRIPS any
client `x-forwarded-for`/`x-real-ip` and stamps the real `socket.remoteAddress`
into a trusted header. The proxy forwards that to wolf-server over mTLS (so
wolf-server trusts it because it arrives from the authenticated dashboard-client
cert). wolf-server's gate then enumerates its own NIC CIDRs and checks the real
client IP against them. **The hook already exists:** `api/auth.py verify-invite`
has a `# Phase 6.5-h.2 hook: same-network gate goes here` comment marking the one
spot the CIDR check drops in (raise 403 WITHOUT consuming the token on network
failure, so the user can retry from the right network).

**Cost / why its own slice:** the custom server touches the dev launcher
(`scripts/dev.mjs`, currently `next dev --experimental-https`), the standalone
packaging (`output: standalone` ships `.next/standalone/server.js`), the
SSE-streaming path, and the HTTPS cert wiring — so it deserves its own integrity
gate + web-test rather than riding on the verify flow.

**Out of scope even for h.2 (later):** operator-configurable
`WOLF_TRUSTED_ADDITIONAL_CIDRS` (cloud/multi-network deploys); v1 is dynamic
NIC-enumeration only. SMTP-based invites are a separate future phase.

Related: [[web-first-configurability]], [[integrity-across-the-stack]],
[[input-validation-exception-handling]].
