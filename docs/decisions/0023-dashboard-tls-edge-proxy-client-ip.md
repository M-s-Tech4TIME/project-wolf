# 0023 — Dashboard TLS edge proxy for real-client-IP propagation (Phase 6.5-h.2)

**Date:** 2026-06-16
**Status:** accepted
**Decider:** mixed (operator chose the architecture; design by claude-code)
**Related:** [0018](0018-bootstrap-superuser-rbac-login.md) item 9 (the same-network gate this enables), [0016](0016-wolf-component-architecture-and-packaging.md) (single-origin dashboard, mTLS, standalone packaging), `docs/10-build-roadmap.md` item 9b, `services/dashboard/scripts/edge-proxy.mjs`

## Context

ADR 0018 item 9 pairs invite-link verification with a **same-network gate** —
an account may only flip to `verified` from inside Wolf's network. The
verification flow shipped in 6.5-h; the gate was deferred because it cannot be a
one-line check:

- The browser only ever talks to **wolf-dashboard** (single-origin, ADR 0016).
  The dashboard proxy forwards to wolf-server, so wolf-server sees the
  *dashboard's* connection IP (loopback when co-located), not the browser's.
- The browser's real IP is observable only by something that **terminates the
  browser's TLS connection** — i.e. the dashboard tier.
- Next 16's `NextRequest` exposes **no socket / no `.ip`** to route handlers, and
  Next's node server does `req.headers['x-forwarded-for'] ??= socket.remoteAddress`
  — the `??=` **preserves a client-supplied XFF**, so reading XFF inside Next is
  spoofable (a script with a stolen cookie+token sets its own `X-Forwarded-For`).

So a component Wolf controls must own the socket and stamp a trusted header.

## Decision

Front an **unmodified** Next server with a small **TLS edge proxy**
(`services/dashboard/scripts/edge-proxy.mjs`, Node stdlib only):

1. The proxy terminates TLS on the public bind, reads `socket.remoteAddress`,
   **strips** any inbound `x-wolf-client-ip` / `x-forwarded-for` / `x-real-ip`,
   **stamps** `X-Wolf-Client-IP: <real ip>` (+ `x-forwarded-proto: https`), and
   forwards to `next dev` / the standalone `server.js` on a **loopback inner
   port**. Next runs 100% stock — Turbopack dev and `output: standalone` prod are
   untouched (the chosen trade-off vs a custom Next server, which would have
   entangled both the dev Turbopack path and standalone dependency-tracing).
   Responses are streamed (`pipe`, no buffering) so the SSE chat stream still
   flushes token-by-token; WebSocket upgrades (dev HMR) are spliced through.
2. The existing dashboard proxy (`app/api/[...path]/route.ts`) forwards that
   header to wolf-server over **mTLS** (the dashboard-client cert).
3. wolf-server (`wolf_server/network/local_network.py`) enumerates its own NIC
   CIDRs (via `ifaddr`) plus loopback, and at `verify-invite` checks the client
   IP against them — but **trusts `X-Wolf-Client-IP` only when the request is
   mTLS-authenticated as the dashboard** (`request.state.mtls_cert_cn`);
   otherwise it falls back to the real TCP peer. A direct caller to wolf-server
   has no dashboard cert, so the header can't be forged. Out-of-network → 403
   **without consuming the token** (retry from the right network).
4. The gate is **OFF by default**. It is intrinsically an *on-prem,
   single-network* control (membership in wolf-server's network); in an **MSSP**
   deployment wolf-server lives in the provider's datacenter while client orgs
   are remote, so a default-ON gate would permanently block every remote client
   from verifying. MSSP being a first-class target, OFF is the safe default;
   on-prem single-network operators opt in with `SAME_NETWORK_GATE_ENABLED=1`.
   The startup banner prints the live state. Today the flag is env-only; a
   future **Superuser config-settings system** (DB source of truth ⇄ Web
   Settings GUI ⇄ Wolf CLI ⇄ env, Superuser-only, audited — implements ADR 0019)
   turns it into a synced toggle, and **per-org trusted networks** (each org's
   own CIDRs) is the MSSP-correct evolution of the gate itself.

Both launch paths use the one proxy module: `dev.mjs` spawns `next dev` on the
inner port + the proxy; the prod shim runs the proxy, which spawns the
standalone server on the inner port. Packaging ships `edge-proxy.mjs` alongside
`server.js`; TLS + ports come from `/etc/wolf-dashboard/env`.

## Alternatives considered

- **Custom Next server** (`next()` programmatic API + `node:https`). Rejected as
  higher blast-radius: it entangles the dev Turbopack path and, with
  `output: standalone`, requires hand-copying the server into the standalone tree
  + manual dependency tracing. The edge proxy leaves Next entirely stock.
- **Read `X-Forwarded-For` inside a Next route handler.** Rejected — spoofable
  (Next's `??=` preserves a client-supplied XFF); it would be security theater.
- **Enforce the gate at the dashboard tier.** Rejected — "Wolf's network" is
  wolf-server's NICs (distributed deploys differ), and the authoritative,
  audited decision belongs in wolf-server. The dashboard only reports the IP.
- **`psutil` for NIC enumeration.** Rejected in favour of `ifaddr` (pure-Python,
  no C extension) to keep the lean-wheels posture (ADR 0007).

## Consequences

- The dashboard's runtime topology gains one hop (TLS terminator → loopback
  Next) in both dev and prod; the proxy is the single TLS terminator and Next
  runs plain HTTP on loopback.
- wolf-server can authoritatively answer "is this browser on my network?" for
  the verification gate, with the trust rooted in the existing mTLS boundary.
- New surface to maintain: one stdlib-only proxy module + a config flag. No new
  runtime Python deps beyond `ifaddr`.
- Rollback: restore the prior `next dev --experimental-https` launcher + the
  `exec node server.js` shim; the gate flag defaults off when the header path is
  absent (falls back to the TCP peer), so nothing locks out.
- Deferred: operator-configurable trusted-CIDR allowlist
  (`WOLF_TRUSTED_ADDITIONAL_CIDRS`) and a Superuser GUI toggle for the gate.
