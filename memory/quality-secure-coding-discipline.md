---
name: quality-secure-coding-discipline
description: "2026-05-31 user directive: features-first development with quality + secure coding as an in-line discipline at every slice. Dedicated hardening / audit pass deferred to after feature demands are satisfied, but never abandoned."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

**The rule (set 2026-05-31, after the user reflected on accumulated
gaps and felt overwhelmed by the 100-item list dumped in one go):**

Feature development continues at the planned pace. Quality and
secure coding are applied AS THE CODE IS WRITTEN, not deferred to a
separate hardening sweep.

**While building any slice — non-negotiable disciplines:**
  - Organization scoping enforced on every new data path. If a query
    touches `organization_id`, it goes through the existing scoping layer.
    If a new endpoint reads / writes organization-bound data, it derives
    `organization_id` from `OrganizationContext`, never from the request body.
  - Input validation via Pydantic schemas at every API boundary.
    No raw `dict[str, Any]` from request bodies into business logic.
  - SQL via SQLAlchemy ORM / parameterised statements. NEVER string-
    concat user input into SQL.
  - No hardcoded secrets. `SECRET_KEY`-style values come from the
    secrets backend.
  - Tool calls stay read-only in the orchestrator. The CI guard that
    blocks `execute_*` imports stays in place; new tools follow.
  - Frontend treats every API response as untrusted text (no
    `dangerouslySetInnerHTML` unless explicitly justified and reviewed).
  - Meaningful names, no dead code, no orphan refs, no leaks. If a
    feature gets reverted, all its plumbing comes out with it (e.g.
    the in-conversation Find removal in commit ebbe186 — 7 files,
    -632 lines, no stragglers).
  - If a security regression is spotted mid-feature, fix inline.
    That's "not introducing a bug", not "hardening".

**What's explicitly deferred (tracked, not abandoned):**
  - `npm audit` / `pip-audit` vulnerability triage
  - Rate limiting, CSP, CSRF, security headers
  - Secret-default refusal-to-start, cookie `Secure` flag audit,
    HSTS, CSRF tokens, account lockout, MFA
  - Audit-log tamper-evidence
  - Performance hardening (memo, virtualisation, bundle tree-shake,
    `model.delta` batching, query timeouts, embedding cache, etc.)
  - End-to-end / Playwright tests
  - Threat-model document refresh
  - Container hardening, deployment runbook

These items are catalogued in the conversation context above. After
feature demands are satisfied, they get a dedicated phase (likely
labelled `5.0d` or similar). The user's words: *"build and
development first, security later, but never ever left behind
neither avoided."*

**For me (Claude), specifically:**
  - Don't dump 100-item gap lists in one go. When I find issues,
    group them by *blocks MVP* / *should-have* / *post-MVP* so the
    user sees relevant scope, not the firehose.
  - When iteration burns cycles on the wrong thing (Find feature
    burned 6 rewrites before deletion), propose pulling the cord
    earlier — not after 6 attempts.
  - The per-slice integrity gate stays. The dedicated security /
    perf hardening passes are SEPARATE work, scheduled later.
