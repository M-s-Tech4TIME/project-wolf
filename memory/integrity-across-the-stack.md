---
name: integrity-across-the-stack
description: "Standing rule — every change must preserve integrity across frontend, backend, DB, libraries, UI; nothing breaks because of any other change"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

The user set this as a standing rule on 2026-05-30 after the verification pass on Slice 5.0c-d caught a real silent regression I had shipped in Slice 5.0c-a: the new `[verified]` chip token broke an end-to-end test expectation in `test_chat_endpoint.py`, but my per-slice subset of tests didn't include that file, so it slipped through.

**Rule:** every change — UI, backend, DB, library, code, or otherwise — must strictly preserve integrity across the whole stack. Nothing must be hindered or hampered because of any other change.

**Why:** The user has built a lot. Trust is the load-bearing asset. A "this slice is UI-only" assumption that turns out to be wrong costs both the immediate regression AND credibility on every subsequent slice.

**How to apply:** Before any commit:

1. **Backend tests — full suite, not the per-slice subset.**
   `.venv/bin/python -m pytest services/orchestrator/tests/ -q -p no:cacheprovider`
   The known environmental fail (`test_factory_accepts_sentence_transformers_aliases` — CUDA OOM when Ollama holds the GPU) is acceptable to ignore as long as **it is the only failure** and the reason is confirmed environmental. Everything else must pass.

2. **Cross-tenant isolation gate** — the CI-explicit safety suite.
   `.venv/bin/python -m pytest services/orchestrator/tests/test_cross_tenant_isolation.py services/orchestrator/tests/test_tenant_scoped_cache.py -v`
   All 18 must pass. This is non-negotiable; it's the multi-tenancy guarantee.

3. **Lint + types.**
   `.venv/bin/ruff check services/` and the CI-exact `uv run mypy …` invocation.

4. **Frontend tsc + eslint** on EVERY change, not just frontend slices.
   `cd frontend && npx tsc --noEmit && npx eslint <touched>`

5. **Live self-validation** — at minimum a login round-trip. For slices that change loop behaviour, also a direct API probe (curl or a small Python script) of the changed path.

6. **DB / schema:** if a slice ever changes a migration, run `alembic upgrade head` and back down once to confirm reversibility before committing.

7. **Libraries:** new dependencies must go through `uv add` (Python) or `npm install` (frontend) — never inline manual edits to `pyproject.toml` / `package.json`. Prefer existing deps; `httpx.MockTransport` was used in Slice 5.0c-d to avoid pulling `respx`.

8. **Commit message** must name everything the change actually touched, even incidentally. "Frontend-only" is a claim, not a label.

**What the user has explicitly accepted as out-of-scope for this rule:**
- Environmental flakes (CUDA OOM on a GPU also serving Ollama) that are reproducibly NOT my code.
- Long latencies caused by hardware (the 14-min cold qwen3:4b + 8b swap acknowledged in ADR 0015) — these are operational, not integrity, concerns.

**What I've explicitly committed to going forward** (from the verification pass that caught the 5.0c-a silent regression):
- ANY change in `services/` triggers the FULL orchestrator suite, not a subset.
- I run cross-tenant isolation as a smoke check, even on UI-only slices.
- I no longer use the phrase "frontend-only" as a reason to skip backend tests; I run them anyway.
