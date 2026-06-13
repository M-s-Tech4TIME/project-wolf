---
name: dependabot-uv-lock-only-prs
description: "Dependabot uv \"requirement update\" PRs edit only uv.lock, not pyproject — check `uv lock --check` after merging them"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

Dependabot's uv-ecosystem "Update X requirement" PRs (seen 2026-06: #11 python-jose, #12 uvicorn, #14 pydantic, #15 sqlalchemy) changed ONLY uv.lock — they rewrote the lock's `requires-dist` lines without touching `services/server/pyproject.toml`, leaving manifest/lock drift. CI masks it because `uv sync` silently re-locks; `uv lock --check` exposes it. Fixed 2026-06-13 (commit 33f11f9) by landing all four floors in the manifest by hand.

**Why:** the lock's pinned versions satisfy the security intent, but the manifest floor is what survives a future re-resolve — without it the fix can silently regress.

**How to apply:** after merging any Dependabot "requirement update" PR for the uv workspace, run `uv lock --check`; if it fails, copy the intended floor from the PR title into the relevant pyproject and re-lock. Related: [[repo-public-hosted-ci]].
