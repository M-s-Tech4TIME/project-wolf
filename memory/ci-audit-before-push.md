---
name: ci-audit-before-push
description: "STANDING RULE (2026-06-11) — before EVERY push, audit .github/workflows/* for changes the current slice/phase makes necessary (paths, job lists, strict-mypy set, smoke checks, shipped artifacts); apply them in the same commit so CI reaches a complete stable closeout with the push, never after it."
metadata:
  node_type: memory
  type: feedback
---

STANDING RULE (2026-06-11): the operator directed — verbatim intent — "from now
on before pushing, check whether the CI-related things need any changes or
modifications relevant to the current slices or phases that we worked with. If
any changes or modifications are necessary or required, do accordingly — make
sure CI-related things get a complete stable checkout/closeout."

**Why:** the Phase 6.4 push failed two CI jobs (alembic-check + smoke-mtls)
because ci.yml still referenced renamed paths, and a follow-up commit was
needed. The operator wants CI co-evolved with the slice, not patched after a
red run.

**How to apply — pre-push checklist (run before EVERY `git push`):**

1. `grep` ci.yml (+ release.yml, dependabot.yml, .gitleaks.toml,
   .github/scripts/) for every path / module / file the slice renamed,
   added, or removed.
2. New Python packages → consider the strict-mypy set in BOTH ci.yml's
   typecheck job AND the Makefile typecheck target (keep them in parity).
3. New shipped artifacts (.deb contents, /usr/bin shims) → extend
   smoke-deb-install's verification loops; check git file modes (100755
   for executables).
4. New migrations → alembic-check + smoke-mtls run them on a FRESH
   Postgres; verify against a clean DB, not just the aged dev DB
   (see the Phase 6.4 FK-naming lesson).
5. New test files → confirm directory discovery picks them up; named
   explicit gates (isolation suite) need path updates when files rename.
6. Where feasible, run the CI job's EXACT command locally before pushing
   (mypy invocation, coverage floor command, isolation gate).
7. After push: watch the run (`gh run watch`) until fully green — the
   slice is not closed until CI is.

Related: [[integrity-across-the-stack]], [[no-unaddressed-errors]],
[[periodic-plan-sync]].
