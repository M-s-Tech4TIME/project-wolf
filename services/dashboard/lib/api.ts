// Thin client for the wolf-server HTTP API.
//
// All requests carry the session cookie (`credentials: "include"`).  The
// cookie is set by wolf-server on login (HTTP-only, samesite=lax).
// For dev (localhost:3000 ↔ localhost:7860), CORS is pre-configured on
// wolf-server with allow_credentials. Note: the cross-origin
// NetworkError this setup produces under HTTPS is tracked as a known
// issue in PROGRESS.md §6 — resolution is Phase 5.6 (edge-component
// reverse proxy via Next.js API routes, removes the second origin).
//
// Phase 5.5 rename: NEXT_PUBLIC_ORCHESTRATOR_URL → NEXT_PUBLIC_SERVER_URL
// (the env var name follows the component rename from "orchestrator" to
// "server"). Operators with the old name in their .env.local should
// rename it; the old name is no longer honored.

import type {
  ChatRequestBody,
  ChatResponseBody,
  LoginRequest,
  LoginResponse,
  LoopEvent,
  MeResponse,
  TenantMembership,
} from "./types";

// Resolve at call time, not module load: in the browser we use whichever
// host the page was served from (so the LAN IP follows whatever the user
// typed in the address bar), with port 7860. `NEXT_PUBLIC_SERVER_URL`
// overrides for production / pinned deploys.
function apiBase(): string {
  if (process.env.NEXT_PUBLIC_SERVER_URL) {
    return process.env.NEXT_PUBLIC_SERVER_URL;
  }
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:7860`;
  }
  return "http://localhost:7860";
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function apiFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  return fetch(`${apiBase()}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...(init.headers ?? {}),
    },
  });
}

async function unwrap<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let body: unknown;
    let detail = resp.statusText;
    try {
      body = await resp.json();
      if (body && typeof body === "object" && "detail" in body) {
        detail = String((body as { detail: unknown }).detail);
      }
    } catch {
      detail = await resp.text();
    }
    throw new ApiError(resp.status, detail, body);
  }
  return resp.json() as Promise<T>;
}

// ── Auth ─────────────────────────────────────────────────────────────────

export function login(body: LoginRequest): Promise<LoginResponse> {
  return apiFetch("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify(body),
  }).then(unwrap<LoginResponse>);
}

export async function logout(): Promise<void> {
  await apiFetch("/api/v1/auth/logout", { method: "POST" });
}

export async function fetchMe(): Promise<MeResponse | null> {
  const resp = await apiFetch("/api/v1/auth/me");
  if (resp.status === 401) return null;
  return unwrap<MeResponse>(resp);
}

export function fetchMyTenants(): Promise<TenantMembership[]> {
  return apiFetch("/api/v1/auth/me/tenants").then(
    unwrap<TenantMembership[]>,
  );
}

// ── Chat ─────────────────────────────────────────────────────────────────

export function chat(body: ChatRequestBody): Promise<ChatResponseBody> {
  return apiFetch("/api/v1/chat", {
    method: "POST",
    body: JSON.stringify(body),
  }).then(unwrap<ChatResponseBody>);
}

/**
 * Stream chat events via SSE.  Calls `onEvent` for every loop event.
 * Returns when the stream completes (wolf-server sends `event: done`).
 *
 * Throws ApiError on HTTP-level failures (401 before the stream starts,
 * 500 from the backend, etc.).  Network or parse errors during streaming
 * bubble as plain Error.
 */
export async function chatStream(
  body: ChatRequestBody,
  onEvent: (event: LoopEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`${apiBase()}/api/v1/chat/stream`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok) {
    throw new ApiError(resp.status, `chat stream failed: ${resp.statusText}`);
  }
  if (!resp.body) {
    throw new Error("chat stream: response had no body");
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line (\n\n).
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const event = parseFrame(frame);
      if (event && event.type !== "done") {
        onEvent(event as LoopEvent);
      }
    }
  }
}

function parseFrame(frame: string): { type: string; data: unknown } | null {
  let eventName: string | null = null;
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }
  if (!eventName) return null;
  const data = dataLines.length > 0 ? JSON.parse(dataLines.join("\n")) : {};
  return { type: eventName, data };
}
