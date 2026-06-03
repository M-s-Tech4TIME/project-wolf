// Reverse proxy: browser → wolf-dashboard → wolf-server.
//
// Phase 5.6-a (per ADR 0016): wolf-dashboard is the only origin the
// browser ever talks to. Every `/api/v1/...` request the dashboard's
// client code makes hits THIS handler, which forwards to wolf-server
// (resolved from the server-side `WOLF_SERVER_URL` env var, default
// `http://localhost:7860`). Phase 5.6-c will add mTLS between this
// proxy and wolf-server using a leaf cert minted by wolf-cert.
//
// Why this exists: before 5.6-a, the browser saw two Wolf origins
// (`http://host:3000` for the dashboard + `http://host:7860` for
// wolf-server). Under HTTPS that second origin's self-signed cert
// produced a silent cross-origin NetworkError after the user
// click-throughed the dashboard's warning. Folding everything into
// a single origin removes the second origin entirely.
//
// Streaming: the SSE chat stream MUST pass through as a
// `ReadableStream` (no `await response.text()`-style buffering),
// or token-by-token rendering would degrade to "wait for the whole
// answer." We hand `response.body` straight back to the browser.

import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
// Use undici's fetch directly: Node 24's global `fetch` is a wrapper
// that strips the undici-specific `dispatcher` option we need to
// pass the Wolf CA trust into the outbound TLS handshake (see
// `loadDispatcher()` below). Verified empirically: global `fetch`
// + `dispatcher` fails with "invalid onRequestStart method"; undici's
// `fetch` accepts it.
import { Agent, fetch as undiciFetch } from "undici";
import { NextRequest } from "next/server";

// Resolve wolf-server's URL once at module load.
//
// Precedence:
//   1. `WOLF_SERVER_URL` env var — operator override, distributed
//      deployments will set this explicitly (e.g.
//      `https://wolf-server.acme.internal:7860`).
//   2. Cert-presence auto-detect — if
//      `<repo>/.local/certs/server/cert.pem` exists, wolf-server's
//      Phase 5.4-c launcher has flipped it to HTTPS, so we point at
//      `https://localhost:7860` instead of HTTP. Mirrors the
//      cert-files-are-the-signal pattern from the launcher itself.
//   3. Plain HTTP fallback for the no-certs dev path.
function resolveServerUrl(repoRoot: string): string {
  if (process.env.WOLF_SERVER_URL) return process.env.WOLF_SERVER_URL;
  const serverCert = resolve(repoRoot, ".local/certs/server/cert.pem");
  if (existsSync(serverCert)) return "https://localhost:7860";
  return "http://localhost:7860";
}

// cwd is the dashboard package root when next-dev runs; the repo
// root is two levels up. Match scripts/dev.mjs's anchoring.
const REPO_ROOT = resolve(process.cwd(), "..", "..");
const WOLF_SERVER_URL = resolveServerUrl(REPO_ROOT);

// Trust the Wolf CA + (Phase 5.6-c) present the dashboard's client
// leaf to wolf-server.
//
// Next.js spawns its `next-server` worker with a sanitized env that
// strips `NODE_EXTRA_CA_CERTS` (the parent `next dev` process has
// it, the worker doesn't), so we can't rely on Node's global CA
// trust mechanism for the proxy fetch. Build an undici Dispatcher
// with the CA loaded explicitly and pass it via the `dispatcher`
// option on each fetch() call.
//
// Phase 5.6-c: when the dashboard-client leaf
// (`.local/certs/dashboard-client/{cert,key}.pem`, minted by
// `wolf-cert init` per slice 5.6-b) is present, we also load it
// into the Agent's `connect` block. undici then presents this cert
// during the outbound TLS handshake to wolf-server, satisfying
// wolf-server's CERT_OPTIONAL + MtlsMiddleware policy. If the
// client leaf is absent, we still trust the CA but don't present a
// cert; wolf-server's middleware will reject the request with 401
// (the dev-no-mTLS path requires no certs anywhere, so this only
// happens in a half-configured state).
function loadDispatcher(): Agent | undefined {
  const caPath = resolve(REPO_ROOT, ".local/certs/ca/ca-cert.pem");
  if (!existsSync(caPath)) return undefined;
  const connect: {
    ca: string;
    cert?: string;
    key?: string;
  } = { ca: readFileSync(caPath, "utf-8") };
  const clientCertPath = resolve(
    REPO_ROOT,
    ".local/certs/dashboard-client/cert.pem",
  );
  const clientKeyPath = resolve(
    REPO_ROOT,
    ".local/certs/dashboard-client/key.pem",
  );
  if (existsSync(clientCertPath) && existsSync(clientKeyPath)) {
    connect.cert = readFileSync(clientCertPath, "utf-8");
    connect.key = readFileSync(clientKeyPath, "utf-8");
  }
  return new Agent({ connect });
}

const WOLF_DISPATCHER = loadDispatcher();

// Hop-by-hop headers per RFC 7230 §6.1 — these must NOT be forwarded
// because they describe the single transport-layer hop, not the
// end-to-end semantics. Plus `host`, which the upstream sets itself.
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
  // Let fetch() compute these from the actual body:
  "content-length",
]);

function filterHeaders(src: Headers): Headers {
  const out = new Headers();
  src.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase()) && key.toLowerCase() !== "set-cookie") {
      out.set(key, value);
    }
  });
  // `Set-Cookie` is special: multiple headers must NOT be collapsed
  // because the values contain commas (in Expires=) that would be
  // ambiguous as a header-list separator. `Headers.forEach` flattens
  // them into one comma-joined string and would lose the second
  // cookie. `getSetCookie()` (Node ≥18, browsers' fetch) returns each
  // original Set-Cookie header line as its own array entry — append
  // them one by one onto the response.
  for (const cookie of src.getSetCookie()) {
    out.append("set-cookie", cookie);
  }
  return out;
}

async function proxy(
  req: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await context.params;
  const search = req.nextUrl.search;
  const upstream = `${WOLF_SERVER_URL}/api/${path.join("/")}${search}`;

  // For GET/HEAD/DELETE the body must be omitted entirely; fetch
  // otherwise throws "Request with GET/HEAD method cannot have body."
  const hasBody = !["GET", "HEAD", "DELETE"].includes(req.method);

  let upstreamResp: Response;
  try {
    // undici's `body` type doesn't include `ReadableStream` in its
    // public types, but it accepts one at runtime (that's what
    // `duplex: "half"` is for — streaming the request body up to
    // the upstream). Cast through `unknown` to satisfy TS.
    const init = {
      method: req.method,
      headers: filterHeaders(req.headers),
      body: hasBody ? req.body : undefined,
      duplex: hasBody ? "half" : undefined,
      dispatcher: WOLF_DISPATCHER,
      redirect: "manual" as const,
      signal: req.signal,
    };
    const undiciResp = await undiciFetch(
      upstream,
      init as unknown as Parameters<typeof undiciFetch>[1],
    );
    // undici's Response is Web-spec-compatible; cast to the global
    // Response type so the rest of the handler can construct
    // `new Response(upstreamResp.body, …)` against the standard API.
    upstreamResp = undiciResp as unknown as Response;
  } catch (err) {
    // wolf-server unreachable, DNS failure, mid-flight abort, etc.
    if (req.signal.aborted) {
      // Client gave up; return 499 (nginx convention for client-closed-request).
      return new Response(null, { status: 499 });
    }
    const detail = err instanceof Error ? err.message : String(err);
    return Response.json(
      { error: "bad_gateway", detail: `wolf-server unreachable: ${detail}` },
      { status: 502 },
    );
  }

  // Pass the upstream response through verbatim: status, headers
  // (filtered), and body (as a ReadableStream so SSE flushes in
  // real time instead of being buffered to completion).
  return new Response(upstreamResp.body, {
    status: upstreamResp.status,
    statusText: upstreamResp.statusText,
    headers: filterHeaders(upstreamResp.headers),
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
export const OPTIONS = proxy;

// Next.js by default tries to statically analyze route handlers
// for caching; this one is intrinsically dynamic (it forwards live
// state), so force the dynamic runtime.
export const dynamic = "force-dynamic";
// Use the Node.js runtime (not edge): we need the streaming-fetch
// behaviour that the Node runtime gives us via undici, including
// the `duplex: "half"` option for streaming request bodies.
export const runtime = "nodejs";
