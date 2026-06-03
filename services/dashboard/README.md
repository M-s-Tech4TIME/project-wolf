# wolf-dashboard

Next.js 16 (App Router) + Tailwind 4 + shadcn/ui. The edge component
per [ADR 0016](../../docs/decisions/0016-wolf-component-architecture-and-packaging.md)
— the only Wolf process that browsers talk to directly.

Phase 5.6-a (2026-06-03) made this the **only** origin the browser
ever sees: every `/api/v1/...` request hits Next.js's catch-all
route handler at [`app/api/[...path]/route.ts`](app/api/[...path]/route.ts),
which reverse-proxies to wolf-server (resolved from the
server-side `WOLF_SERVER_URL` env var, default
`http://localhost:7860`). The browser never makes a cross-origin
fetch, which is what killed the HTTPS-mode NetworkError from Phase
5.4. Phase 5.6-c will layer mTLS on top so the proxy presents a
client cert to wolf-server.

Phase 5.5 rename: this package was `frontend/` (package name
`frontend`) pre-2026-06-03; now it's `services/dashboard/` (package
name `wolf-dashboard`) to align with the Wazuh-style component
naming.

## Quickstart

```bash
nvm use            # picks up Node 24 LTS from .nvmrc
npm install
cp .env.example .env.local
npm run dev        # auto-HTTPS when .local/certs/dashboard/{cert,key}.pem
                   # exist (after `wolf-cert init`); HTTP fallback otherwise
                   # — see scripts/dev.mjs
```

Make sure wolf-server is running too:

```bash
# From the repo root, in another shell:
cd services/server
set -a && source ../../.env && set +a
uv run python -m wolf_server   # the Phase 5.4-c launcher; auto-HTTPS when certs present
```

You will also need a bootstrapped tenant (`uv run python -m
wolf_server.management.bootstrap_tenant ...` — see
[`services/server/wolf_server/management/bootstrap_tenant.py`](../server/wolf_server/management/bootstrap_tenant.py)).

## Structure

```
app/
  layout.tsx            root layout — wraps everything in <AuthProvider>
  page.tsx              redirects to /chat or /login based on auth
  login/page.tsx        unauthenticated login form
  chat/
    layout.tsx          auth-gates the chat surface
    page.tsx            mounts <ChatShell>

components/
  auth-provider.tsx     /me + /me/tenants context
  chat-shell.tsx        header + sidebar + main + citations
  chat-header.tsx       app title + tenant switcher + user menu
  chat-sidebar.tsx      history of past conversations (+ Starred section)
  chat-composer.tsx     question input + send / stop button
  chats-history-overlay.tsx  full-screen search across every branch
  message-thread.tsx    user/assistant bubbles + tree branching navigator
  citations-panel.tsx   tool calls + citations (right panel)
  markdown.tsx          markdown renderer + syntax highlighting + verdict chips
  tenant-switcher.tsx   dropdown that triggers re-login on switch
  login-form.tsx        email/password (+ optional tenant override)
  confirm-dialog.tsx    in-app destructive-action confirmation
  ui/                   shadcn primitives

hooks/
  use-conversation-streams.ts   per-conversation SSE state manager (Phase 5.0c-k)

lib/
  api.ts                fetch wrapper + endpoint methods + SSE parser
  branches.ts           conversation-tree helpers (Phase 5.0c-l)
  types.ts              TypeScript types mirroring wolf-server's Pydantic schemas
  clipboard.ts          secure-context API + execCommand fallback
  format.ts             time/number formatting
  uuid.ts               browser-safe UUID generation
  utils.ts              cn() class helper

scripts/
  dev.mjs               Phase 5.4-d launcher — auto-HTTPS based on cert presence
```

## Notes on auth

- wolf-server sets HTTP-only `wolf_access_token` and `wolf_refresh_token`
  cookies with `samesite=lax`. Post-5.6-a the browser only sees one
  origin (the dashboard at :3000), so the cookies are scoped to that
  single origin — no cross-port eTLD+1 gymnastics needed.
- All `fetch` calls use `credentials: "include"` (see `lib/api.ts`).
- The tenant switcher does not re-issue JWTs server-side (yet). Switching
  tenants signs out and sends the user back to `/login?tenant=<id>` with
  the desired tenant prefilled.

## SSE streaming

`useConversationStreams` posts to `/api/v1/chat/stream` and parses the
event stream in `lib/api.ts:chatStream`. The hook keeps independent state
per conversation (`StreamState` slice) so multiple conversations can
stream in parallel. `MessageThread` shows the live progress;
`CitationsPanel` shows tool calls and citations in the right rail.

## Conversation tree branching (Phase 5.0c-l)

Each user / assistant message is its own node in a tree. Edit on a
user message creates a sibling user node; Retry on an assistant
message creates a sibling assistant node. `lib/branches.ts` owns the
tree primitives — `fork()`, `appendChildOf()`, `switchToSibling()`,
`activePathNodes()` etc. — with runtime invariant assertions that
make the v4-era "merged sibling set" bug impossible to reintroduce.
