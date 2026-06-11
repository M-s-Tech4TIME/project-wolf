---
name: no-unaddressed-errors
description: "STANDING RULE (2026-06-01) — never leave errors, warnings, or silenced diagnostics unaddressed; fix them, maintain integrity across the project and across error-handling itself"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

STANDING RULE (set 2026-06-01): No error, warning, or silently-skipped diagnostic is allowed to remain unaddressed in this project. Going forward I must fix them, solve them, or — only if a fix is genuinely out of scope for the current slice — explicitly track them with a concrete plan to address them. "Pre-existing baseline" is not an acceptable answer; the user pushed back specifically on me brushing past 56 mypy `import-untyped` errors that had been present since Phase 0 just because they weren't *introduced* by the current slice.

This sits alongside [[integrity-across-the-stack]] and [[quality-secure-coding-discipline]] — those rules cover the *positive* integrity bar (everything we add must be coherent across frontend / backend / DB / libs / UI); this rule covers the *negative* bar: nothing broken or warning is left lying around.

**Scope of "errors":**
- compiler / type-checker errors (tsc, mypy)
- linter errors (eslint, ruff)
- test failures (pytest, frontend tests)
- runtime warnings and console errors during web verification
- silently-suppressed import errors, missing type stubs, `# type: ignore` without justification
- error-handling integrity: handlers that swallow exceptions, broad `except:`, returns that fail silently, missing structured error logging on security-relevant paths

**Why:** Wolf is a security tool. Silent typing blind spots like the Phase-0 missing `py.typed` markers meant mypy was giving us 56 errors' worth of false-confidence across the orchestrator for the entire project lifetime. Leaving diagnostics unfixed compounds — each one trains the eye to ignore the next one until none of them mean anything. For a tool whose users will trust it with multi-organization security data, that posture is not acceptable.

**How to apply:**
- After every integrity gate, *every* non-zero count gets either a fix in this slice OR a one-line entry in the deferred backlog with a concrete plan.
- Never report "X errors, unchanged from baseline" as if it's a pass. State the count, state when they first appeared, state the plan.
- If a fix is small and high-leverage (like adding `py.typed` markers), do it inline as a standalone pre-commit before the feature slice — don't bundle it into the slice and don't defer it.
- For error-handling code specifically (exception handlers, error responses, security-event logging), treat any change that *removes* error visibility as a regression even if tests pass.
- This applies retroactively to pre-existing debt the user encounters or that I surface during integrity checks — the "I didn't introduce it" defence is closed.
