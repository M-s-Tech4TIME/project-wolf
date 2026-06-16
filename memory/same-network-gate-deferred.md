---
name: same-network-gate-deferred
description: "6.5-h.2 SHIPPED 2026-06-16 (ADR 0018 item 9; topology in ADR 0023): the same-network verification gate. A stdlib TLS edge proxy fronts STOCK Next, owns the browser socket + stamps a trusted X-Wolf-Client-IP; wolf-server trusts it ONLY under mTLS and CIDR-checks it vs its own NIC CIDRs. Gate OFF by default (it's on-prem-only; default-ON would block remote MSSP clients); SAME_NETWORK_GATE_ENABLED=1 enables. Synced Superuser toggle + per-org networks are follow-ups."
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

SHIPPED 2026-06-16 (was deferred from 6.5-h). ADR 0018 item 9's "verify only
from inside Wolf's network" gate. Topology decision recorded in **ADR 0023**.

**The problem (why it wasn't a one-line check):** the browser only ever talks to
wolf-dashboard (single-origin, ADR 0016); the proxy forwards to wolf-server, so
wolf-server sees the dashboard's IP, not the browser's. Next 16 exposes no
socket to route handlers, and its `x-forwarded-for ??= socket.remoteAddress`
PRESERVES a client-supplied XFF → reading XFF inside Next is spoofable.

**What shipped — TLS edge proxy (NOT a custom Next server; operator-approved):**
- `services/dashboard/scripts/edge-proxy.mjs` (Node stdlib only) terminates TLS
  on the public bind, STRIPS inbound x-wolf-client-ip/x-forwarded-for/x-real-ip,
  STAMPS the real `socket.remoteAddress` as `X-Wolf-Client-IP` (+ x-forwarded-
  proto https), forwards to an UNMODIFIED `next dev` / standalone `server.js` on
  a loopback inner port. Streams responses (SSE preserved) + splices WS upgrades
  (HMR). Chosen over a custom Next server to keep Turbopack-dev +
  output:standalone-prod 100% stock (the custom server's standalone dependency-
  tracing was the riskier path).
- `scripts/dev.mjs` rewired (drops --experimental-https; next on inner port +
  proxy on public bind). Prod: the shim runs the proxy (spawns server.js inner);
  `debian/wolf-dashboard.install` ships edge-proxy.mjs alongside server.js; unit
  comments + postinst env hint updated (WOLF_DASHBOARD_TLS_CERT/_KEY, PORT,
  WOLF_DASHBOARD_INNER_PORT). smoke-deb-install asserts the proxy file ships.
- wolf-server: `wolf_server/network/local_network.py` enumerates NIC CIDRs via
  **`ifaddr`** (pure-Python, lean-wheels per ADR 0007) + loopback;
  `client_ip_in_local_network()` fails closed + normalises v4-mapped-v6.
  `verify-invite` resolves the IP via `_resolve_gate_client_ip` — trusts
  `X-Wolf-Client-IP` ONLY when mTLS-authenticated as the dashboard
  (`request.state.mtls_cert_cn`), else the real TCP peer — then CIDR-checks it.
  Out-of-network → 403 `wrong_network` WITHOUT consuming the token (retry from
  the right network). Flag `same_network_gate_enabled` (Settings, default
  **False** — MSSP-safe; `SAME_NETWORK_GATE_ENABLED=1` enables on on-prem
  single-network deploys); startup banner prints the state.
  `wolf_server/network` added to the mypy strict-set (ci.yml + Makefile).

**Trust anchor:** a direct caller to wolf-server:7860 has no dashboard-client
cert → mtls_cert_cn unset → the header is ignored → falls back to its real
(non-local) TCP peer → blocked. The edge proxy overwriting the header
unconditionally defeats browser-side spoofing. Validated in isolation
(strip/stamp + no-buffering) + live (HTTPS + login over mTLS).

**WHY DEFAULT OFF (MSSP, operator-surfaced 2026-06-16):** the gate checks
membership in *wolf-server's* network. In an MSSP deployment wolf-server lives
in the provider's datacenter while client orgs are remote → a default-ON gate
permanently blocks every remote client from verifying. MSSP is a first-class
target, so OFF is the safe default; on-prem single-network operators opt in.
The gate ships as inert-but-ready machinery (the edge proxy / IP-propagation is
the reusable substrate both the toggle and per-org networks need).

**Follow-ups (operator-approved):**
- A **synced Superuser toggle** for the gate → comes with the config-settings
  system, [[config-settings-system-phase]] (Phase 6.10, ADR 0019). Env-only here.
- **Per-org trusted networks** (each org's own CIDRs; verification checks the
  user's IP vs THEIR org's networks) = the MSSP-correct evolution of the gate.
  Supersedes the old `WOLF_TRUSTED_ADDITIONAL_CIDRS` idea.

**DEFERRED OPERATOR WEB-TEST — DO NOT SKIP (operator, 2026-06-16):** because
6.5-h.2 shipped the gate *inert* (default-OFF), it was NOT operator-web-tested
end-to-end. When the gate is activated — likely at [[config-settings-system-phase]]
(Phase 6.10) when the Superuser toggle lands, or whenever an operator enables it —
the WHOLE feature must get a full web-test, owed and not to be skipped:
on-network verify succeeds; off-LAN device → 403 `wrong_network` with the token
NOT consumed (retry works on-network); spoofed `X-Wolf-Client-IP` is ignored;
chat SSE still streams through the edge proxy. This obligation rides until that
test is actually performed. (Standing rule: no test is skipped — see
[[no-unaddressed-errors]].)

Related: [[config-settings-system-phase]], [[superuser-config-authority]],
[[web-first-configurability]], [[integrity-across-the-stack]],
[[wolf-bootstrap-superuser-flow]], [[no-unaddressed-errors]].
