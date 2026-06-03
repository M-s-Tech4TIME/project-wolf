# Wolf frontend

Next.js 16 (App Router) + Tailwind 4 + shadcn/ui.  Talks to the orchestrator
on `http://localhost:8000` by default.

## Quickstart

```bash
nvm use            # picks up Node 24 LTS from .nvmrc
npm install
cp .env.example .env.local
npm run dev        # http://localhost:3000
```

Make sure the orchestrator is running too:

```bash
# From the repo root, in another shell:
cd services/orchestrator && uv run uvicorn app.main:app --reload
```

You will also need a bootstrapped tenant (`uv run python -m
app.management.bootstrap_tenant ...` — see `services/orchestrator/app/
management/bootstrap_tenant.py`).

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
  chat-sidebar.tsx      history of past exchanges
  chat-composer.tsx     question input + send
  message-thread.tsx    user/assistant bubbles + streaming progress
  citations-panel.tsx   tool calls + citations (right panel)
  tenant-switcher.tsx   dropdown that triggers re-login on switch
  login-form.tsx        email/password (+ optional tenant override)
  ui/                   shadcn primitives

hooks/
  use-chat-stream.ts    SSE state machine; consumes /api/v1/chat/stream

lib/
  api.ts                fetch wrapper + endpoint methods + SSE parser
  types.ts              TypeScript types mirroring the orchestrator
  utils.ts              cn() class helper
```

## Notes on auth

- The orchestrator sets HTTP-only `wolf_access_token` and `wolf_refresh_token`
  cookies with `samesite=lax`.  In dev across ports (3000 ↔ 8000), the
  cookies flow because both share the eTLD+1 `localhost`.
- All `fetch` calls use `credentials: "include"` (see `lib/api.ts`).
- The tenant switcher does not re-issue JWTs server-side (yet).  Switching
  tenants signs out and sends the user back to `/login?tenant=<id>` with
  the desired tenant prefilled.

## SSE streaming

`useChatStream` posts to `/api/v1/chat/stream` and parses the event stream
in `lib/api.ts:chatStream`.  The hook exposes `status`, `toolEvents`,
`citations`, and the final `exchange`.  `MessageThread` shows the live
progress; `CitationsPanel` shows tool calls and citations in the right rail.
