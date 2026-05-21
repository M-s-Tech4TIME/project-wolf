// Thin client for the Wolf orchestrator HTTP API.
//
// All requests carry the session cookie (`credentials: "include"`).  The
// cookie is set by the orchestrator on login (HTTP-only, samesite=lax).
// For dev (localhost:3000 ↔ localhost:8000), CORS is pre-configured on the
// orchestrator with allow_credentials.

import type {
  ChatRequestBody,
  ChatResponseBody,
  LoginRequest,
  LoginResponse,
  LoopEvent,
  MeResponse,
  TenantMembership,
} from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_ORCHESTRATOR_URL ?? "http://localhost:8000";

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
  return fetch(`${API_BASE}${path}`, {
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
 * Returns when the stream completes (the orchestrator sends `event: done`).
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
  const resp = await fetch(`${API_BASE}/api/v1/chat/stream`, {
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
