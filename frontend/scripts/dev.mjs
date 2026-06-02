#!/usr/bin/env node
/**
 * Frontend dev-server launcher — Phase 5.4-d.
 *
 * Mirrors the orchestrator's auto-HTTPS posture (Phase 5.4-c): if the
 * frontend TLS cert + key files exist under <repo>/.local/certs/frontend/,
 * start `next dev` with `--experimental-https --experimental-https-cert
 * <cert> --experimental-https-key <key>` so the Next.js dev server
 * serves over TLS. Otherwise fall back to plain `next dev` (today's
 * HTTP-on-localhost dev shape).
 *
 * The cert files themselves are the signal — `wolf-cert init` mints
 * both the orchestrator and frontend leaves under the same CA, so the
 * next `npm run dev` start auto-upgrades to HTTPS. `wolf-cert revoke`
 * removes them; the launcher drops back to HTTP. No env flag toggles
 * between the two.
 *
 * Why a wrapper script rather than two npm scripts?
 *   - Single source of truth for the cert paths (matches Phase 5.4-c's
 *     posture).
 *   - Operators don't have to remember which command to run after a
 *     `wolf-cert init`; the existing `npm run dev` keeps working.
 *   - Discovers + reports the chosen scheme on stdout so the operator
 *     sees what the dev server picked without having to read source.
 */

import { existsSync, statSync } from "node:fs";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));

// frontend/scripts/dev.mjs → ../../  is the repo root. The
// orchestrator launcher uses the same anchoring strategy in
// `services/orchestrator/app/config.py`.
const REPO_ROOT = resolve(__dirname, "..", "..");
const CERT_PATH = resolve(REPO_ROOT, ".local/certs/frontend/cert.pem");
const KEY_PATH = resolve(REPO_ROOT, ".local/certs/frontend/key.pem");

function isFile(p) {
  try {
    return existsSync(p) && statSync(p).isFile();
  } catch {
    return false;
  }
}

const certPresent = isFile(CERT_PATH);
const keyPresent = isFile(KEY_PATH);

const extraArgs = [];
if (certPresent && keyPresent) {
  extraArgs.push(
    "--experimental-https",
    "--experimental-https-cert", CERT_PATH,
    "--experimental-https-key", KEY_PATH,
  );
  console.log(`wolf-frontend: serving HTTPS via Next.js --experimental-https`);
  console.log(`  cert: ${CERT_PATH}`);
  console.log(`  key:  ${KEY_PATH}`);
} else if (certPresent !== keyPresent) {
  // Broken pair — surface it loudly and fall back to HTTP. A half-
  // loaded TLS config in Next.js produces obscure handshake failures
  // far away from the cause; better to refuse and explain.
  const missing = certPresent ? KEY_PATH : CERT_PATH;
  console.warn(
    `wolf-frontend: TLS pair incomplete — ${missing} is missing; falling back ` +
    "to HTTP. Run `wolf-cert renew` (or `wolf-cert init`) to regenerate.",
  );
} else {
  console.log(
    "wolf-frontend: no TLS cert at " + CERT_PATH + " — starting on HTTP. " +
    "Run `wolf-cert init` to mint a self-signed pair and the next " +
    "`npm run dev` will auto-upgrade.",
  );
}

// Forward any extra CLI args from the operator (e.g. `npm run dev --
// --port 4000`).
const operatorArgs = process.argv.slice(2);

const child = spawn(
  "next",
  ["dev", ...extraArgs, ...operatorArgs],
  { stdio: "inherit", shell: false },
);

child.on("exit", (code) => {
  process.exit(code ?? 0);
});

// Forward SIGINT / SIGTERM so Ctrl+C in the parent shell still stops
// the child cleanly (npm scripts can otherwise eat the signal).
for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    child.kill(sig);
  });
}
