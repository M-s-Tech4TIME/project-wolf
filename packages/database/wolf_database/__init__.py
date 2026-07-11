"""wolf-database — Wolf's bundled-Postgres component (Phase 5.7).

Per ADR 0016, wolf-database is the third deployable component. It
wraps a system-installed Postgres 18 + pgvector with Wolf-owned
config, data dir, and lifecycle scripts. The actual Postgres binaries
come from the OS package manager (postgresql-18 +
postgresql-18-pgvector) so the security-update path stays apt/dnf;
Wolf owns everything *around* the binaries.

This package ships the substrate (Phase 5.7-a): paths, constants,
config-template rendering, binary-discovery helpers. The
`wolf-database` CLI built on top of it lands in Phase 5.7-b.
"""

from wolf_database.layout import (
    DB_NAME_DEFAULT,
    DB_USER_DEFAULT,
    DatabaseLayout,
    resolve_layout,
)

__all__ = [
    "DB_NAME_DEFAULT",
    "DB_USER_DEFAULT",
    "DatabaseLayout",
    "resolve_layout",
]
