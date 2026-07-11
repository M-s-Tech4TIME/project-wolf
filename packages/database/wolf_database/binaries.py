"""Locate the system-installed Postgres binaries wolf-database wraps.

Per the 5.7 architecture decision (use system Postgres + Wolf-owned
config + data, NOT bundle binaries), wolf-database expects
postgresql-18 and postgresql-18-pgvector to be installed via apt /
dnf. This module finds the relevant binaries (`pg_ctl`, `initdb`,
`psql`, `postgres`) and reports their version + provenance, so the
CLI in 5.7-b can surface clear "you need to install postgresql-18"
errors instead of cryptic FileNotFoundErrors.

Lookup order for each tool:
  1. The corresponding env var (e.g. `WOLF_DATABASE_PG_CTL`).
  2. Distro-specific known paths (`/usr/lib/postgresql/18/bin/...`
     on Debian/Ubuntu, `/usr/pgsql-18/bin/...` on RHEL/Fedora).
  3. `shutil.which()` on the bare tool name (catches unusual
     installs where Postgres is on PATH).

The function returns the first hit. If nothing resolves we raise
`PostgresBinaryNotFoundError` with the searched paths in the
exception message — operators see exactly what wolf-database looked
for and where.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

REQUIRED_MAJOR_VERSION = 18


class PostgresBinaryNotFoundError(RuntimeError):
    """Raised when a Postgres binary required by wolf-database is missing.

    Carries the binary name + the list of paths that were searched so
    the operator-facing CLI can surface a useful error message
    ("install postgresql-18") rather than a cryptic FileNotFoundError.
    """

    def __init__(self, tool: str, searched: list[Path]) -> None:
        self.tool = tool
        self.searched = searched
        msg = (
            f"wolf-database could not find `{tool}`. Searched:\n  - "
            + "\n  - ".join(str(p) for p in searched)
            + "\nInstall PostgreSQL 18 + pgvector via your distro's "
            "package manager (e.g. `apt install postgresql-18 "
            "postgresql-18-pgvector` on Debian/Ubuntu) or set "
            f"WOLF_DATABASE_{tool.upper()} to the absolute path."
        )
        super().__init__(msg)


# Distro-specific known prefixes where Postgres 18 lives. Ordered:
# Debian-family first (most common Wolf dev target per ADR 0008),
# then RHEL-family. Each entry is the directory containing the
# binaries — we append the tool name when searching.
_KNOWN_BIN_DIRS: tuple[Path, ...] = (
    Path(f"/usr/lib/postgresql/{REQUIRED_MAJOR_VERSION}/bin"),  # Debian/Ubuntu
    Path(f"/usr/pgsql-{REQUIRED_MAJOR_VERSION}/bin"),  # RHEL/Fedora
)


@dataclass(frozen=True)
class PostgresBinaries:
    """Resolved absolute paths to the Postgres binaries we use."""

    pg_ctl: Path
    initdb: Path
    psql: Path
    postgres: Path


def _env_override_for(tool: str) -> Path | None:
    """Read WOLF_DATABASE_<TOOL> from env; return None if unset/empty."""
    val = os.environ.get(f"WOLF_DATABASE_{tool.upper()}")
    if not val:
        return None
    return Path(val)


def _find_one(tool: str) -> Path:
    """Locate a single Postgres binary or raise PostgresBinaryNotFoundError.

    Search order documented at the module top: env override, then
    distro-known dirs, then PATH.
    """
    searched: list[Path] = []

    override = _env_override_for(tool)
    if override is not None:
        searched.append(override)
        if override.is_file() and os.access(override, os.X_OK):
            return override.resolve()

    for bin_dir in _KNOWN_BIN_DIRS:
        candidate = bin_dir / tool
        searched.append(candidate)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()

    via_path = shutil.which(tool)
    if via_path is not None:
        candidate = Path(via_path)
        searched.append(candidate)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    else:
        searched.append(Path(f"$PATH:{tool}"))

    raise PostgresBinaryNotFoundError(tool=tool, searched=searched)


def find_postgres_binaries() -> PostgresBinaries:
    """Resolve all four Postgres binaries we need; raise on any miss.

    All-or-nothing: a partial install (e.g. pg_ctl but no initdb) is
    a configuration error wolf-database can't usefully cope with, so
    we fail fast at the first missing tool. The caller's exception
    handler can format the error for the operator.
    """
    return PostgresBinaries(
        pg_ctl=_find_one("pg_ctl"),
        initdb=_find_one("initdb"),
        psql=_find_one("psql"),
        postgres=_find_one("postgres"),
    )


_VERSION_RE = re.compile(r"\(PostgreSQL\)\s+(\d+)(?:\.(\d+))?")


def postgres_major_version(postgres: Path) -> int:
    """Run `postgres --version` and return the major version as an int.

    `postgres --version` prints e.g. `postgres (PostgreSQL) 17.1`.
    We parse the first integer after `(PostgreSQL)`. If the output
    doesn't match the expected shape we raise ValueError — that's a
    distro packaging anomaly and we'd rather fail loudly than guess.
    """
    result = subprocess.run(  # noqa: S603  pg_ctl-found path; not shell injection
        [str(postgres), "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    match = _VERSION_RE.search(result.stdout)
    if match is None:
        raise ValueError(
            f"unexpected `postgres --version` output: {result.stdout!r}",
        )
    return int(match.group(1))


def verify_postgres_supported(binaries: PostgresBinaries) -> None:
    """Check that the system Postgres is the required major version.

    Wolf depends on Postgres 18+ features (per ADR 0008's pgvector
    + Postgres 18 commitment). Running against an older major is a
    silent footgun — alembic might "work" but the schema would diverge
    from what wolf-server expects. Verify upfront and fail loudly.
    """
    major = postgres_major_version(binaries.postgres)
    if major < REQUIRED_MAJOR_VERSION:
        raise RuntimeError(
            f"wolf-database requires PostgreSQL "
            f"{REQUIRED_MAJOR_VERSION}+, found {major} at "
            f"{binaries.postgres}. Install postgresql-"
            f"{REQUIRED_MAJOR_VERSION} via your distro's package "
            "manager.",
        )
