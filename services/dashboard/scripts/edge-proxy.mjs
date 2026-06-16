#!/usr/bin/env node
/**
 * wolf-dashboard TLS edge proxy — Phase 6.5-h.2 (ADR 0018 item 9).
 *
 * Why this exists: the same-network verification gate needs the browser's
 * REAL IP, and only something that terminates the browser's TCP connection
 * can observe it. Next 16 hides the socket from route handlers, and its
 * `x-forwarded-for ??= socket.remoteAddress` preserves a client-supplied
 * XFF — so reading XFF inside Next is spoofable. This tiny proxy sits in
 * front of an UNMODIFIED Next server (dev or standalone), owns the TLS
 * socket, and stamps a trusted `X-Wolf-Client-IP` from `socket.remoteAddress`
 * after stripping any client-supplied IP headers. wolf-server then trusts
 * that header only because it arrives over mTLS from the dashboard client
 * (see services/server/wolf_server/api/auth.py verify-invite).
 *
 * Topology (same module, both modes):
 *   dev   → scripts/dev.mjs spawns `next dev` on 127.0.0.1:INNER + this proxy
 *           on 0.0.0.0:PORT with the dashboard cert/key.
 *   prod  → run directly (`node edge-proxy.mjs` from /usr/lib/wolf-dashboard/);
 *           spawns the standalone `server.js` on 127.0.0.1:INNER + this proxy
 *           on the public bind with TLS from env.
 *
 * Node stdlib only (no node_modules) so it ships into the standalone tree
 * without dependency tracing.
 */

import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { createServer as createHttpServer, request as httpRequest } from "node:http";
import { createServer as createHttpsServer } from "node:https";
import { connect as netConnect } from "node:net";
import { fileURLToPath } from "node:url";

// Headers that assert the caller's network identity. Only THIS proxy (it
// owns the TCP socket) may set the client IP; anything a client sent is a
// forgery attempt and is stripped before the request reaches Next.
const CLIENT_IP_HEADER = "x-wolf-client-ip";
const SPOOFABLE_IP_HEADERS = [CLIENT_IP_HEADER, "x-forwarded-for", "x-real-ip"];

function normaliseIp(addr) {
  if (!addr) return "";
  // ::ffff:192.0.2.1 → 192.0.2.1 so it matches wolf-server's IPv4 NIC CIDRs.
  if (addr.startsWith("::ffff:") && addr.includes(".")) return addr.slice("::ffff:".length);
  return addr;
}

/** Strip client-supplied IP headers and stamp the real socket IP. */
function stampClientIp(headers, socket) {
  for (const h of SPOOFABLE_IP_HEADERS) delete headers[h];
  const ip = normaliseIp(socket.remoteAddress || "");
  if (ip) headers[CLIENT_IP_HEADER] = ip;
  // The browser reached us over TLS; tell Next so proto-aware code is right.
  headers["x-forwarded-proto"] = "https";
}

/**
 * Start the edge proxy. Returns the http(s).Server.
 *
 * @param {object} opts
 * @param {string} [opts.bindHost="0.0.0.0"]
 * @param {number} [opts.bindPort=3000]
 * @param {string} [opts.innerHost="127.0.0.1"]
 * @param {number} [opts.innerPort=3001]
 * @param {string} [opts.certPath]  TLS cert (PEM); HTTP if absent
 * @param {string} [opts.keyPath]   TLS key (PEM)
 * @param {(info: object) => void} [opts.onListen]
 */
export function startEdgeProxy(opts = {}) {
  const {
    bindHost = "0.0.0.0",
    bindPort = 3000,
    innerHost = "127.0.0.1",
    innerPort = 3001,
    certPath,
    keyPath,
    onListen,
  } = opts;

  const tls = Boolean(certPath && keyPath && existsSync(certPath) && existsSync(keyPath));

  const handler = (req, res) => {
    stampClientIp(req.headers, req.socket);
    const upstream = httpRequest(
      {
        host: innerHost,
        port: innerPort,
        method: req.method,
        path: req.url,
        headers: req.headers,
      },
      (proxyRes) => {
        // Pass the response through verbatim. `pipe` streams the body as it
        // arrives — the SSE chat stream flushes token-by-token instead of
        // being buffered to completion.
        res.writeHead(proxyRes.statusCode || 502, proxyRes.headers);
        proxyRes.pipe(res);
      },
    );
    upstream.on("error", (err) => {
      // Inner server not up yet / crashed / mid-flight abort.
      if (!res.headersSent) {
        res.writeHead(502, { "content-type": "application/json" });
        res.end(
          JSON.stringify({ error: "bad_gateway", detail: `next unreachable: ${err.message}` }),
        );
      } else {
        res.destroy();
      }
    });
    req.pipe(upstream);
  };

  const server = tls
    ? createHttpsServer({ cert: readFileSync(certPath), key: readFileSync(keyPath) }, handler)
    : createHttpServer(handler);

  // WebSocket upgrades (Next dev's HMR channel). Re-stamp the client IP,
  // then splice the raw sockets together. Reconstruct the request line +
  // headers from rawHeaders so duplicates / casing survive; drop the
  // spoofable IP headers and add the stamped one.
  server.on("upgrade", (req, clientSocket, head) => {
    const ip = normaliseIp(req.socket.remoteAddress || "");
    const lines = [`${req.method} ${req.url} HTTP/1.1`];
    for (let i = 0; i < req.rawHeaders.length; i += 2) {
      const name = req.rawHeaders[i];
      if (SPOOFABLE_IP_HEADERS.includes(name.toLowerCase())) continue;
      lines.push(`${name}: ${req.rawHeaders[i + 1]}`);
    }
    if (ip) lines.push(`${CLIENT_IP_HEADER}: ${ip}`);

    const upstream = netConnect(innerPort, innerHost, () => {
      upstream.write(lines.join("\r\n") + "\r\n\r\n");
      if (head && head.length) upstream.write(head);
      clientSocket.pipe(upstream);
      upstream.pipe(clientSocket);
    });
    upstream.on("error", () => clientSocket.destroy());
    clientSocket.on("error", () => upstream.destroy());
  });

  server.listen(bindPort, bindHost, () => {
    onListen?.({ scheme: tls ? "https" : "http", bindHost, bindPort, innerHost, innerPort });
  });
  return server;
}

// ── Direct-run (production) mode ─────────────────────────────────────────────
// Spawn the Next.js standalone server on the inner loopback port, then front
// it with the edge proxy on the public bind. Used by the shipped
// /usr/bin/wolf-dashboard shim from /usr/lib/wolf-dashboard/.
function runProd() {
  const publicHost = process.env.HOSTNAME || "0.0.0.0";
  const publicPort = Number(process.env.PORT || 3000);
  const innerPort = Number(process.env.WOLF_DASHBOARD_INNER_PORT || 3001);
  const certPath = process.env.WOLF_DASHBOARD_TLS_CERT || undefined;
  const keyPath = process.env.WOLF_DASHBOARD_TLS_KEY || undefined;

  // Next standalone reads PORT + HOSTNAME from env. Pin it to the inner
  // loopback port; the proxy owns the public bind.
  const child = spawn(process.execPath, ["server.js"], {
    stdio: "inherit",
    env: { ...process.env, HOSTNAME: "127.0.0.1", PORT: String(innerPort) },
  });

  startEdgeProxy({
    bindHost: publicHost,
    bindPort: publicPort,
    innerPort,
    certPath,
    keyPath,
    onListen: ({ scheme }) => {
      console.log(
        `wolf-dashboard: edge proxy serving ${scheme}://${publicHost}:${publicPort} ` +
          `→ next (standalone) on 127.0.0.1:${innerPort}`,
      );
      if (!certPath || !keyPath) {
        console.log(
          "  TLS: DISABLED — set WOLF_DASHBOARD_TLS_CERT + WOLF_DASHBOARD_TLS_KEY for HTTPS",
        );
      }
    },
  });

  child.on("exit", (code) => process.exit(code ?? 0));
  for (const sig of ["SIGINT", "SIGTERM"]) {
    process.on(sig, () => child.kill(sig));
  }
}

// ESM "is this module the entry point?" check.
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  runProd();
}
