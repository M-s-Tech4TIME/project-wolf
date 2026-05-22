# 11 — Instructions for Claude Code

This document is written directly to the coding agent (Claude Code or equivalent)
that will implement the system. **Read this before writing any code.**

## How to use this bundle

These documents are the **authoritative source of truth** for the project. If a
user asks you to do something that contradicts them, surface the contradiction and
ask, don't silently override. If you discover a gap, flag it in a note and propose
the right way to fill it before writing code.

Read the documents in numerical order. Do not skim. The architecture is
opinionated, and the opinions are load-bearing — they exist because of specific
security and operational concerns described in the docs.

## MANDATORY — Session continuity protocol

This applies to **every** Claude Code session, on every device, without
exception. Two files at `docs/PROGRESS.md` and `docs/CHANGELOG.md` are the
project's memory across sessions. They are how a brand-new session, on a
brand-new machine, can pick up where the last one left off.

### At the start of every session

**Reading PROGRESS.md and CHANGELOG.md is required only in these cases:**

1. **A brand-new Claude Code session** — no continuity with prior turns.
2. **A different development environment** — different machine, different
   checkout, different VM, different OS. Anything where your view of the
   codebase or running services might differ from what was true last time.
3. **A different Claude model or model version** than the one that ended
   the previous session.

In any of those cases, before responding to the user's request, before
reading any code, before doing anything else:

1. **Read `docs/PROGRESS.md` in full.** This is the live state — what's built,
   what's broken, what's next, what configuration is in effect, what
   decisions are pending. It is short by design.
2. **Read the most recent 3-5 entries in `docs/CHANGELOG.md`.** This is the
   recent history — what happened in the last few sessions and what was
   decided.
3. **In your first response to the user, briefly confirm** what phase the
   project is in and what the immediate "What's next" items are, per
   PROGRESS.md. One short paragraph. This proves you have the context and
   gives the user a chance to correct any drift before you start work.

If `docs/PROGRESS.md` or `docs/CHANGELOG.md` is missing on a fresh checkout,
surface that as the first action — don't proceed without project memory.

**In a continuous session** (same conversation, same machine, same model)
re-reading PROGRESS.md every turn is unnecessary noise — the live state is
already in context. The end-of-session update is still mandatory regardless.

### During the session

Maintain a running mental note of: what you changed, what you decided, what
broke, what you discovered. You will write these down at the end of the
session — don't lose them. If the session is long, periodically jot them
into a scratch note.

### At the end of every session

Before considering the session complete:

1. **Update `docs/PROGRESS.md`**. Specifically:
   - The "Last updated" line at the top.
   - Section 1 ("Where we are right now") — phase, status.
   - Section 2 ("What's currently built and working") — flip statuses, add
     new items, remove items that are no longer relevant.
   - Section 4 ("What's next") — replace with the new next-actions.
   - Section 5 ("Active decisions and open questions") — add anything new;
     remove anything resolved.
   - Section 7 ("Test coverage status") — update if tests changed.
   - Section 9 ("Hand-off note") — write a fresh hand-off note for the next
     session.

   PROGRESS.md should stay **short** — under ~250 lines. If it grows beyond
   that, condense and move history into CHANGELOG.md.

2. **Append a new entry to `docs/CHANGELOG.md`** using the template at the
   top of that file. Specifically:
   - Date (YYYY-MM-DD).
   - Session brief title.
   - Session type, phase, approximate duration, ending branch/commit.
   - "What we did" — concrete actions taken.
   - "What we decided" — including any new ADRs.
   - "What broke / what we discovered" — unexpected findings.
   - "What's next" — should mirror PROGRESS.md section 4.

3. **Commit PROGRESS.md and CHANGELOG.md updates as the LAST commit of the
   session.** Use a commit message like `docs: update PROGRESS and CHANGELOG
   for session YYYY-MM-DD`. This ensures the project memory is preserved
   even if the session ends abruptly.

### Why this matters

The user explicitly designed this project to be **resumable across sessions,
across devices, and across model versions**. A new Claude Code session
starting fresh on a different machine should be able to read PROGRESS.md and
the last few CHANGELOG.md entries and become productive within minutes
without re-asking the user for context. **You are accountable for keeping
that promise true.** Skipping the update at session end breaks the contract
the project depends on.

## Working style

### Before you write code

1. **Confirm understanding** of the slice you are about to build. If the task is
   "implement Phase 2," restate the exit criteria from `10-build-roadmap.md` and
   confirm before starting.
2. **Identify which docs apply** and re-read them. Phase 2 touches `01`, `02`,
   `03`, `05`, `07` — all of them.
3. **Surface ambiguity early.** If a doc says "should" but doesn't say "how,"
   ask. Do not invent a design and proceed.

### As you write code

1. **Type everything.** Pydantic models for tool I/O, typed function signatures,
   no `Any` in load-bearing code.
2. **Test the negative path.** For every safety-relevant feature, include a test
   that confirms the bad thing **cannot** happen. Cross-tenant access tests,
   execute-tool-from-orchestrator tests, ungrounded-claim tests.
3. **Don't optimize prematurely.** Get the boring, correct version working.
   Performance comes later. Safety comes first, always.
4. **Audit-log generously.** When in doubt, write the event. Storage is cheap;
   forensic gaps are not.
5. **Resist scope creep.** If you find yourself adding a feature not in the
   current phase, stop and ask whether it belongs.

### After you finish a slice

1. **Run the full test suite** — including the cross-tenant isolation suite.
2. **Update the docs** if your implementation revealed something the planning
   bundle missed. The bundle is not sacred; it is the best snapshot of what was
   known when it was written. If reality teaches you something, document it.
3. **Hand off cleanly:** what was built, what was tested, what is left, and any
   new ambiguity you uncovered.

## Hard rules — never violate

These are derived from the architecture docs and are non-negotiable.

### Rule 1 — Execute tools live only in the gateway service

The orchestrator process must not import, expose, or be capable of running an
execute tool. Execute tools are physically in the `services/gateway/` package and
the orchestrator has no code path to them. If you find yourself writing
`execute_active_response` in the orchestrator, stop. You are off the path.

### Rule 2 — The model never picks the tenant

The `tenant_id` is taken from the authenticated session and injected by the
orchestrator into every tool call. If a tool's input schema contains a
`tenant_id` field that comes from the model's output, that is a bug. Tenant ID
is not in the model-facing schema.

### Rule 3 — All factual claims must be grounded

Any answer the agent produces that makes a factual claim about the deployment
(an agent's state, an alert's details, a rule's behavior) must trace to a real
tool result or a real retrieved chunk. The grounding validator enforces this.
Do not disable it. Do not weaken it to make a test pass; fix the underlying
issue.

### Rule 4 — Untrusted input stays untrusted

Log content, file content, retrieved chunks, user-agent strings, hostnames —
all of it is **data the agent reads**, never **instructions the agent
follows**. The capability-tier design contains this, but you must not write
code that treats retrieved content as authoritative for action.

### Rule 5 — Tool schemas are strict, both ways

Validate inputs against the schema before calling the tool. Validate outputs
against the schema before returning to the model. A schema mismatch is an
error, never a "best-effort fix it up."

### Rule 6 — No paid dependency is allowed to be required

The platform must run end-to-end with only open-source, self-hostable
components. Paid options are supported; never required. If you find yourself
reaching for a paid API as the only path, stop and design an open-source
alternative as the default.

### Rule 7 — Audit reach is one-directional

Code in the orchestrator and gateway can **write** to the audit store. Nothing
in either service can **delete** from it or **modify** it. The audit store's
write API accepts only appends. If you need to update a record, you write a new
event referencing the old one — you never mutate.

### Rule 8 — Two services, even when running on one host

The orchestrator and gateway are separate services with separate credentials and
separate roles, communicating via the typed proposal + signed-token protocol. A
"single-process convenience mode" for tiny deployments is allowed only if it
**preserves the same protocol between the two layers internally** — so it can be
split again with no rewrite.

## The structural safety summary — keep this in mind always

The platform's safety story rests on four facts that must remain true at all
times:

1. The model sees only `read` and `propose` tools — `execute` is absent from its
   schema entirely.
2. The orchestrator's dispatch is an allowlist; unknown or execute calls are
   rejected and logged as anomalies.
3. Credentials are scoped such that the data layer would refuse a forbidden
   operation even if facts 1 and 2 both failed.
4. The gateway demands a signed approval token bound to a proposal's content
   hash before executing anything.

If you ever write code that weakens any of these four facts, you are off the
path. Surface it and discuss it. Never silently change them.

## When the user asks for shortcuts

You will be asked to skip pieces of this design. Some examples and how to
respond:

- *"Just let the model execute things directly, it's faster."* — No. The split
  between propose and execute is the entire safety story. Decline and explain.
- *"Skip the cross-tenant tests, they slow CI down."* — No. They are the only
  honest defense against a class of catastrophic bug. Decline and explain.
- *"Just hardcode the tenant for now, we'll fix it later."* — No. Tenant
  context being session-bound is structural. Hardcoding is a foothold for a bug
  that won't be found until production. Decline and explain.
- *"Drop the grounding validator, the answers are fine."* — No. Ungrounded
  security advice is the worst class of error this product can make. Decline
  and explain.

When the user insists on a shortcut after explanation, ask them to record the
deviation in `docs/decisions/` (an ADR) and proceed only with explicit
acknowledgement of what is being given up.

## When you find a real problem in the plan

This bundle is the best version of the plan at the time it was written. It is
not a sacred text. If your hands-on implementation reveals that a design choice
is wrong — not inconvenient, **wrong** — flag it, explain why, and propose the
alternative. The plan should evolve; the principles in `00` should not.

## Coding standards

- **Python:** 3.13.x, managed via `uv` (never raw pip). `ruff` for lint and
  format, `mypy` in strict mode for the safety-critical packages (tenancy,
  capability dispatch, gateway), `pytest` with coverage targets ≥ 80% on those
  same modules.
- **TypeScript:** strict mode on, Next.js 16 App Router, ESLint + Prettier,
  Vitest for unit tests, Playwright for end-to-end. Package management via
  `pnpm`.
- **Node version:** 24 LTS, pinned in `.nvmrc`.
- **Python version:** 3.13.x, pinned in `.python-version`.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`,
  `test:`, `refactor:`).
- **Branches:** trunk-based with short-lived feature branches; `main` is
  always releasable.
- **PRs:** must include tests, must pass CI (including the cross-tenant
  isolation suite), must reference the doc/phase they implement, must not
  introduce a paid-only dependency.

## Documentation expectations

For every meaningful module you produce:

- A short module docstring explaining what it does and why.
- API docs for every public function (auto-generated where possible).
- For load-bearing decisions, a one-paragraph ADR in `docs/decisions/` with the
  context, the decision, and the alternatives considered.

## A note on this document's authority

You will likely receive instructions during implementation that contradict this
document in small ways. That is fine and expected. The order of precedence:

1. **Hard rules in this document** (and the equivalents in `00`-`10`). These
   override everything else.
2. **The architecture and design in `00`-`10`.** Deviations require explicit
   acknowledgement from the user.
3. **The user's specific implementation requests** for the current task.
4. **Your own judgment** about local design choices.

Higher numbers defer to lower numbers. If you are ever asked to do something
that the hard rules forbid, refuse and surface the conflict.
