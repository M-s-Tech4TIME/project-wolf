# Security Policy

## Supported versions

Only the latest release on `main` is actively supported.

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Email the maintainers at the address in `pyproject.toml`. Include:

- Description of the vulnerability.
- Steps to reproduce.
- Potential impact.

You will receive a response within 72 hours. We follow responsible disclosure:
the issue will be fixed before public disclosure, and you will be credited.

## Security architecture

Wolf's safety story is architectural, not prompt-based. Key facts:

- The model sees only `read` and `propose` tools; `execute` tools are absent from its schema.
- The orchestrator's dispatch is an allowlist; unknown calls are rejected and logged.
- Credentials are scoped so the data layer would refuse a forbidden operation even if
  application logic failed.
- The gateway demands a signed, hash-bound approval token before executing anything.

See `docs/07-security-and-threat-model.md` for the full threat model.
