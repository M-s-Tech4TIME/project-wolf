"""Install-time bootstrap tooling (Phase 6.5-a, ADR 0018).

Modules here are operator-on-host CLIs invoked via their shell wrappers
(`deploy/bin/*`), never directly — see the shell-wrapper-required pattern.
"""
