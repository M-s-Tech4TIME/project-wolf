#!/usr/bin/env node
/**
 * wolf-dashboard dev-server launcher — Phase 5.4-d (renamed Phase 5.5),
 * rewired in Phase 6.5-h.2 to front Next with a TLS edge proxy.
 *
 * Topology (Phase 6.5-h.2): the edge proxy
 * (scripts/edge-proxy.mjs) terminates TLS on the public bind, owns the
 * browser's TCP socket, stamps a trusted X-Wolf-Client-IP header (the
 * same-network verification gate needs the real browser IP — Next 16
 * hides the socket from route handlers), and forwards to an UNMODIFIED
 * `next dev` listening on a loopback-only inner port. Next stays 100%
 * stock (Turbopack dev unchanged); all the IP plumbing lives in the
 * proxy.
 *
 * TLS auto-detect (unchanged signal, new terminator): if the dashboard
 * cert + key exist under <repo>/.local/certs/dashboard/, the edge proxy
 * serves HTTPS; otherwise it serves plain HTTP (today's HTTP-on-localhost
 * dev shape). `next dev` itself now always runs plain HTTP on loopback —
 * the `--experimental-https` flag moved to the proxy.
 *
 * The cert files themselves are the signal — `wolf-cert init` mints both
 * the server and dashboard leaves under the same CA. `wolf-cert revoke`
 * removes them; the launcher drops back to HTTP. No env flag toggles
 * between the two.
 *
 * Ports:
 *   public bind  — PORT (default 3000), 0.0.0.0
 *   inner Next   — WOLF_DASHBOARD_INNER_PORT (default 3001), 127.0.0.1
 */

import { spawn } from "node:child_process";
import { existsSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { startEdgeProxy } from "./edge-proxy.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));

// services/dashboard/scripts/dev.mjs → ../../../  is the repo root.
const REPO_ROOT = resolve(__dirname, "..", "..", "..");
const CERT_PATH = resolve(REPO_ROOT, ".local/certs/dashboard/cert.pem");
const KEY_PATH = resolve(REPO_ROOT, ".local/certs/dashboard/key.pem");
const CLIENT_CERT_PATH = resolve(REPO_ROOT, ".local/certs/dashboard-client/cert.pem");
const CLIENT_KEY_PATH = resolve(REPO_ROOT, ".local/certs/dashboard-client/key.pem");
const WOLF_CA_PATH = resolve(REPO_ROOT, ".local/certs/ca/ca-cert.pem");

const PUBLIC_HOST = "0.0.0.0";
const PUBLIC_PORT = Number(process.env.PORT || 3000);
const INNER_HOST = "127.0.0.1";
const INNER_PORT = Number(process.env.WOLF_DASHBOARD_INNER_PORT || 3001);

function isFile(p) {
  try {
    return existsSync(p) && statSync(p).isFile();
  } catch {
    return false;
  }
}

const tlsReady = isFile(CERT_PATH) && isFile(KEY_PATH);
const tlsBroken = isFile(CERT_PATH) !== isFile(KEY_PATH);
// Phase 5.6-c: the proxy in app/api/[...path]/route.ts reads these files
// at module load and presents them via undici Agent. The launcher just
// reports state here so the operator can grep one place to see whether
// mTLS is wired everywhere.
const proxyMtlsReady =
  isFile(CLIENT_CERT_PATH) && isFile(CLIENT_KEY_PATH) && isFile(WOLF_CA_PATH);

if (tlsBroken) {
  // Broken pair — surface it loudly and fall back to HTTP. A half-loaded
  // TLS config produces obscure handshake failures far from the cause.
  const missing = isFile(CERT_PATH) ? KEY_PATH : CERT_PATH;
  console.warn(
    `wolf-dashboard: TLS pair incomplete — ${missing} is missing; falling back ` +
      "to HTTP. Run `wolf-cert renew` (or `wolf-cert init`) to regenerate.",
  );
}

// Start `next dev` on the inner loopback port (plain HTTP). Forward any
// extra operator args (e.g. `npm run dev -- --turbo`). The public
// scheme/port is owned by the edge proxy below.
const operatorArgs = process.argv.slice(2);
const child = spawn(
  "next",
  ["dev", "--hostname", INNER_HOST, "--port", String(INNER_PORT), ...operatorArgs],
  { stdio: "inherit", shell: false },
);

const useTls = tlsReady && !tlsBroken;
const proxy = startEdgeProxy({
  bindHost: PUBLIC_HOST,
  bindPort: PUBLIC_PORT,
  innerHost: INNER_HOST,
  innerPort: INNER_PORT,
  certPath: useTls ? CERT_PATH : undefined,
  keyPath: useTls ? KEY_PATH : undefined,
  onListen: ({ scheme }) => {
    console.log(
      `wolf-dashboard: edge proxy serving ${scheme}://${PUBLIC_HOST}:${PUBLIC_PORT} ` +
        `→ next dev on ${INNER_HOST}:${INNER_PORT}`,
    );
    if (useTls) {
      console.log(`  cert: ${CERT_PATH}`);
      console.log(`  key:  ${KEY_PATH}`);
      console.log(
        proxyMtlsReady
          ? `  proxy mTLS: ENABLED — presenting ${CLIENT_CERT_PATH} as the ` +
              "dashboard-client cert to wolf-server"
          : "  proxy mTLS: DISABLED — dashboard-client cert not on disk " +
              "(run `wolf-cert init` to mint it)",
      );
    } else {
      console.log(
        "  TLS: DISABLED — no dashboard cert at " +
          CERT_PATH +
          ". Run `wolf-cert init` and the next `npm run dev` auto-upgrades.",
      );
    }
  },
});

child.on("exit", (code) => {
  proxy.close();
  process.exit(code ?? 0);
});

// Forward SIGINT / SIGTERM so Ctrl+C still stops the child cleanly, and
// tear the proxy down with it.
for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    proxy.close();
    child.kill(sig);
  });
}
