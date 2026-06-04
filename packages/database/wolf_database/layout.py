"""Filesystem layout + identity constants for wolf-database.

A `DatabaseLayout` is the pure-data record of where wolf-database's
files live on a particular host. There are two canonical layouts:

* **Dev layout** — everything under `<repo>/.local/wolf-database/`,
  mirroring how `.local/certs/` works for wolf-cert. Resolved by
  `resolve_layout(production=False)` (the default for `python -m
  wolf_database`).

* **Production layout** — FHS paths under `/var/lib/wolf-database/`
  (data) and `/etc/wolf-database/` (config), per ADR 0016. Resolved
  by `resolve_layout(production=True)`, or when the env var
  `WOLF_DATABASE_PRODUCTION=1` is set.

Either path is fully overridable via env vars
(`WOLF_DATABASE_DATA_DIR`, `WOLF_DATABASE_CONFIG_DIR`,
`WOLF_DATABASE_SOCKET_DIR`) for operators with non-standard layouts.

The DB name + user names are constants here so every Wolf component
agrees on what to connect to: `wolf` as the DB name and the
role/owner name. Distributed deployments override the connection URL
via `DATABASE_URL` in wolf-server's env, but the wolf-database side
of the contract is fixed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Canonical DB identity — Wolf-side constants. Operators wiring up a
# distributed deployment point their wolf-server at this DB name and
# user. Override via env in wolf-database's own config (Phase 5.7-b)
# only for special-purpose layouts (e.g. shared Postgres cluster).
DB_NAME_DEFAULT = "wolf"
DB_USER_DEFAULT = "wolf"

# Repo-anchor for dev layouts. Resolves at function-call time (not
# import time) so tests can stub it via environment.
def _repo_root() -> Path:
    # `packages/database/wolf_database/layout.py` → up three is repo root.
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class DatabaseLayout:
    """Resolved filesystem layout for one wolf-database instance.

    All paths are absolute. `data_dir`, `config_dir`, and `socket_dir`
    are the three load-bearing locations — every wolf-database
    subcommand reads + writes only inside these directories. Logs go
    to journald (production) or stderr (dev), not to a logfile under
    `data_dir`, so the layout doesn't carry a `log_dir`.
    """

    data_dir: Path
    config_dir: Path
    socket_dir: Path

    # Convenience accessors for the files within these dirs. Pure
    # property — no I/O — so callers can construct paths without
    # touching the filesystem.
    @property
    def postgresql_conf_path(self) -> Path:
        return self.config_dir / "postgresql.conf"

    @property
    def pg_hba_conf_path(self) -> Path:
        return self.config_dir / "pg_hba.conf"

    @property
    def pid_file_path(self) -> Path:
        # pg_ctl writes `postmaster.pid` here; we point pg_ctl at the
        # data dir, so the PID file is always inside it.
        return self.data_dir / "postmaster.pid"


def resolve_layout(*, production: bool | None = None) -> DatabaseLayout:
    """Resolve the layout from env + the production / dev mode.

    Precedence per directory:
      1. The explicit per-dir env var
         (`WOLF_DATABASE_DATA_DIR`, `WOLF_DATABASE_CONFIG_DIR`,
         `WOLF_DATABASE_SOCKET_DIR`).
      2. Production path (`/var/lib/wolf-database/...`) if
         `production=True` or `WOLF_DATABASE_PRODUCTION=1`.
      3. Dev path (`<repo>/.local/wolf-database/...`).
    """
    if production is None:
        production = os.environ.get("WOLF_DATABASE_PRODUCTION", "") == "1"

    if production:
        data_default = Path("/var/lib/wolf-database/data")
        config_default = Path("/etc/wolf-database")
        socket_default = Path("/var/run/wolf-database")
    else:
        base = _repo_root() / ".local" / "wolf-database"
        data_default = base / "data"
        config_default = base / "config"
        socket_default = base / "socket"

    return DatabaseLayout(
        data_dir=Path(
            os.environ.get("WOLF_DATABASE_DATA_DIR") or data_default,
        ).resolve(),
        config_dir=Path(
            os.environ.get("WOLF_DATABASE_CONFIG_DIR") or config_default,
        ).resolve(),
        socket_dir=Path(
            os.environ.get("WOLF_DATABASE_SOCKET_DIR") or socket_default,
        ).resolve(),
    )
