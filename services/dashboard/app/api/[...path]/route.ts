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

import { NextRequest } from "next/server";

const WOLF_SERVER_URL =
  process.env.WOLF_SERVER_URL ?? "http://localhost:7860";

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
    upstreamResp = await fetch(upstream, {
      method: req.method,
      headers: filterHeaders(req.headers),
      body: hasBody ? req.body : undefined,
      // @ts-expect-error — `duplex` is required by undici when streaming
      // a request body, but isn't yet in the standard RequestInit typing.
      duplex: hasBody ? "half" : undefined,
      redirect: "manual",
      signal: req.signal,
    });
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
