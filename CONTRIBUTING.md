# Contributing to Wolf

Wolf is open-source (Apache 2.0) and welcomes contributions.

## Before you start

Read the full planning bundle in `docs/` — particularly `11-claude-code-instructions.md`.
The architecture is opinionated; the opinions are load-bearing.

## Development setup

```bash
# Python (requires uv — https://docs.astral.sh/uv/)
uv sync --all-packages

# Start the stack
make up

# Run migrations
make migrate

# Run tests
make test
```

## Hard rules for contributors

1. No execute tools in wolf-server.
2. Organization context is always session-bound, never from model output.
3. The cross-organization isolation test suite must pass before any PR merges.
4. No paid external dependency may be required.
5. Every factual claim must be grounded; the grounding validator must not be weakened.

See `docs/11-claude-code-instructions.md` for the full list.

## Commit style

[Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`.

## Pull requests

- Must include tests.
- Must pass CI (lint, typecheck, test, isolation suite).
- Must reference the phase/doc they implement.
- Must not introduce a paid-only dependency.
