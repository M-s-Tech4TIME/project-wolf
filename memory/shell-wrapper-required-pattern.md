---
name: shell-wrapper-required-pattern
description: STANDING RULE (2026-06-10) — every Wolf supporting tool ships as Python core + shell wrapper. Python core can ONLY be invoked via the wrapper (direct python invocation is rejected). Security + audit + standardization.
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

STANDING RULE (2026-06-10): Every Wolf supporting tool (CLI utility, admin script, bootstrap helper, recovery tool, smoke runner, etc.) follows the same shape:

- **Python core** at `tools/<name>/<name>.py` (or `packages/<pkg>/<pkg>/cli.py` for component CLIs)
- **Shell wrapper** at `tools/<name>/<name>.sh` (or `deploy/bin/<name>` for `.deb`-shipped wrappers)
- **Wrapper is the ONLY supported invocation path.** Direct `python <tool>.py` invocation is rejected — the Python script asserts `WOLF_WRAPPER_VERSION` env var is set (only the wrapper sets it) and exits non-zero if absent.

## Why

1. **Audit attribution** — the wrapper writes a structured audit event (who-invoked-what-when) BEFORE handing off to Python. Direct invocation would bypass this.
2. **Permission gating** — destructive tools (purge, recover-superuser, etc.) check `EUID == 0` or sudo context in the wrapper. Python scripts don't need to repeat these checks.
3. **Environment preflight** — wrapper validates required env vars, file paths, lock state, etc. before the Python core touches anything. Failures land with a clean error from the wrapper, not a Python traceback.
4. **Confirmation prompts** — destructive ops can require interactive confirmation in the wrapper. Non-interactive contexts (CI, scripts) must pass `--yes` or set a sentinel env var.
5. **Standardised UX** — operators learn ONE invocation pattern (`./tool.sh`), not "is it python tool.py or ./tool.sh this time?"
6. **Future-proofing** — if a tool's invocation needs to change (e.g., wrap in `sudo`, swap interpreter, source extra env), only the wrapper changes; documentation + scripts referencing the tool keep working.

## The wrapper-protocol pattern (concrete)

Inside the wrapper:
```sh
#!/bin/sh
# tools/<name>/<name>.sh
set -eu

# Audit + env validation steps here (specific to the tool)
# ...

export WOLF_WRAPPER_VERSION="1"
exec uv run --project services/server python -m wolf_<name> "$@"
```

Inside the Python core:
```python
import os
import sys

if not os.environ.get("WOLF_WRAPPER_VERSION"):
    print(
        "ERROR: This tool must be invoked via its shell wrapper, not directly.\n"
        "       Run: ./tools/<name>/<name>.sh [args]",
        file=sys.stderr,
    )
    sys.exit(2)
```

## Tools that already follow this pattern (or should)

- `tools/organization_isolation_test/` — needs a wrapper (currently invoked via `make test-isolation` which is wrapper-equivalent)
- `tools/model_probe/` — `make probe` is the de-facto wrapper
- `tools/seed_knowledge/` — `make smoke-database` etc. are de-facto wrappers
- (Future) `bootstrap_superuser.sh` → `bootstrap_superuser.py` — per [[wolf-bootstrap-superuser-flow]]
- (Future) Any new admin-flow CLI tools

## When NOT to apply

- **Component CLIs already shipped via `.deb`** (wolf-cert, wolf-database, wolf-server, wolf-dashboard) — these have `/usr/bin/<name>` shim scripts that act as the wrapper. The shim is the wrapper; the venv-installed Python module is the core. Same pattern, different layout.

## How to apply

- New supporting tool? Build the wrapper FIRST, then the Python core. Document the wrapper invocation in `README.md` / `ONBOARDING.md`. Never document a direct `python` invocation.
- Reviewing a PR that adds a tool? Check for the wrapper + the env-var guard. Reject otherwise.
- Refactoring an existing tool? Add the wrapper if missing.

Related: [[wolf-bootstrap-superuser-flow]] (which explicitly cites this rule for password recovery), [[integrity-across-the-stack]] (which this rule supports for tool-side correctness), [[no-unaddressed-errors]] (the wrapper's audit emission means failed tool invocations get logged, not silently lost).
