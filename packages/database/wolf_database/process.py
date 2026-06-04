"""Subprocess helpers for the wolf-database CLI.

Thin, well-typed wrappers around `pg_ctl`, `initdb`, and `psql`.
Each helper:

* Takes the resolved `PostgresBinaries` + `DatabaseLayout` so it
  doesn't have to re-discover.
* Forwards stdout/stderr to the parent process (operator sees
  Postgres's own output, no swallowing).
* Returns the subprocess's exit code or raises a typed exception
  for use-case-specific failure paths.

We deliberately do NOT capture output. Postgres's startup banner is
useful diagnostic information; eating it for "clean" CLI output
would make troubleshooting harder. The CLI prints a one-line
preamble for each phase ("→ running initdb...") so the operator
sees where Postgres's own output is coming from.

Security note: every `subprocess.run` here uses an explicit list of
arguments (never shell=True). The Postgres binary paths come from
`find_postgres_binaries()`, which resolves them via env override
+ known distro paths + PATH. We pass `# noqa: S603` (subprocess
without shell — the rule's default-deny is for shell=True calls).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wolf_database.binaries import PostgresBinaries
    from wolf_database.layout import DatabaseLayout


class WolfDatabaseError(RuntimeError):
    """A wolf-database operation failed in an operator-actionable way.

    Carries a one-line summary the CLI prints + (optionally) the
    subprocess result so the caller can include exit code / stdout /
    stderr in the message if useful.
    """


@dataclass(frozen=True)
class PgCtlStatus:
    """Result of `pg_ctl status`. Captures the running/stopped state
    plus the PID if running. Postgres's exit codes are documented:
      0 → server is running
      3 → server is not running
      4 → data directory is missing / unreadable
    """

    running: bool
    pid: int | None
    data_dir_ok: bool


# ─── initdb ──────────────────────────────────────────────────────────────


def run_initdb(
    binaries: PostgresBinaries,
    layout: DatabaseLayout,
) -> None:
    """Run `initdb` against `layout.data_dir`. Refuses if the dir is non-empty.

    Auth-method choices, both deliberate:

    * `--auth-local peer` — connections via Unix socket use the
      OS-user identity. The OS user running initdb becomes Postgres
      superuser; that same OS user can connect locally without a
      password. This is the Debian / RHEL default for local
      Postgres installs and it's the right model for wolf-database
      too: production runs as the `wolf-database` system user, dev
      runs as the operator's user — in both cases the local socket
      is a privileged channel by virtue of OS user auth.
    * `--auth-host scram-sha-256` — TCP connections (including
      127.0.0.1) require a password. wolf-server connects this
      way via DATABASE_URL with the password the CLI prints
      during init.

    Caller is responsible for the empty-data-dir precheck before
    this call — we raise WolfDatabaseError on initdb non-zero, but
    initdb's own "directory not empty" error is good enough that
    we just forward it.
    """
    layout.data_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(binaries.initdb),
        "--pgdata", str(layout.data_dir),
        "--auth-host", "scram-sha-256",
        "--auth-local", "peer",
        "--encoding", "UTF8",
        "--locale", "C.UTF-8",
        # initdb defaults the cluster superuser to the OS user
        # running it. That's exactly what we want for dev (operator
        # IS the superuser via local socket). Production's systemd
        # unit runs initdb as the `wolf-database` user, getting the
        # same effect under a dedicated identity.
    ]
    result = subprocess.run(cmd, check=False)  # noqa: S603
    if result.returncode != 0:
        raise WolfDatabaseError(
            f"`initdb` failed with exit {result.returncode}. "
            "See output above.",
        )


# ─── pg_ctl ──────────────────────────────────────────────────────────────


def _pg_ctl(
    binaries: PostgresBinaries,
    layout: DatabaseLayout,
    subcommand: str,
    *,
    extra_args: tuple[str, ...] = (),
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Run `pg_ctl <subcommand> -D <data_dir>` with our config-file flag.

    All pg_ctl invocations carry `-o "--config-file=<our conf>"` so
    Postgres reads our wolf-database-owned postgresql.conf, not the
    file inside the data dir (initdb writes its own there;
    `reconfigure` regenerates the one in `layout.config_dir` and
    `start` uses THAT one via `-o`).
    """
    cmd = [
        str(binaries.pg_ctl),
        subcommand,
        "-D", str(layout.data_dir),
        "-o", f"--config-file={layout.postgresql_conf_path}",
    ]
    cmd.extend(extra_args)
    return subprocess.run(cmd, check=check)  # noqa: S603


def run_pg_ctl_start(
    binaries: PostgresBinaries,
    layout: DatabaseLayout,
    *,
    wait: bool = True,
) -> None:
    """`pg_ctl start` — bring Postgres up.

    `wait=True` (default) makes pg_ctl block until Postgres is
    accepting connections. Use `wait=False` if the caller is going
    to poll readiness itself.
    """
    extra: tuple[str, ...] = ("-w",) if wait else ("-W",)
    result = _pg_ctl(binaries, layout, "start", extra_args=extra, check=False)
    if result.returncode != 0:
        raise WolfDatabaseError(
            f"`pg_ctl start` failed with exit {result.returncode}.",
        )


def run_pg_ctl_stop(
    binaries: PostgresBinaries,
    layout: DatabaseLayout,
    *,
    mode: str = "fast",
) -> None:
    """`pg_ctl stop` — bring Postgres down.

    `mode='fast'` (default) does a SIGINT shutdown — connected
    clients are disconnected, transactions roll back. `'smart'`
    waits for clients to disconnect (operationally a bad default
    for a CLI). `'immediate'` is SIGQUIT — only for emergencies.
    """
    extra = ("-m", mode)
    result = _pg_ctl(binaries, layout, "stop", extra_args=extra, check=False)
    if result.returncode != 0:
        raise WolfDatabaseError(
            f"`pg_ctl stop` failed with exit {result.returncode}.",
        )


def run_pg_ctl_status(
    binaries: PostgresBinaries,
    layout: DatabaseLayout,
) -> PgCtlStatus:
    """`pg_ctl status` — query running state without raising on stopped.

    pg_ctl exits 0 if running, 3 if stopped, 4 if the data dir is
    bad. We map those into PgCtlStatus rather than raising, since
    "stopped" isn't an error condition for status queries.
    """
    if not layout.data_dir.exists():
        return PgCtlStatus(running=False, pid=None, data_dir_ok=False)

    result = subprocess.run(  # noqa: S603
        [str(binaries.pg_ctl), "status", "-D", str(layout.data_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        # "pg_ctl: server is running (PID: 12345)"
        pid = _parse_pid(result.stdout)
        return PgCtlStatus(running=True, pid=pid, data_dir_ok=True)
    if result.returncode == 3:
        return PgCtlStatus(running=False, pid=None, data_dir_ok=True)
    return PgCtlStatus(running=False, pid=None, data_dir_ok=False)


def _parse_pid(stdout: str) -> int | None:
    """Pull the PID out of pg_ctl's `server is running (PID: X)` line."""
    import re  # noqa: PLC0415  local import; this is a one-off helper

    m = re.search(r"PID:\s*(\d+)", stdout)
    return int(m.group(1)) if m else None


# ─── psql ────────────────────────────────────────────────────────────────


def run_psql_command(
    binaries: PostgresBinaries,
    layout: DatabaseLayout,
    *,
    sql: str,
    dbname: str = "postgres",
    user: str | None = None,
    port: int = 5432,
) -> None:
    """Run a single SQL command via psql against the running cluster.

    Connects via the Unix socket in `layout.socket_dir`. Postgres
    names its socket file `.s.PGSQL.<port>`, so we MUST pass `-p`
    matching what Postgres is listening on — otherwise psql looks
    for `.s.PGSQL.5432` (its compiled-in default) and a port-
    overridden cluster appears non-existent. This bug bit the
    5.7-d smoke once.

    No password needed for local socket connections from the same
    OS user — that's `peer` auth, which initdb's `--auth-local peer`
    + our pg_hba's `local all all peer` rule cover.

    `dbname` defaults to `postgres` (the bootstrap DB initdb creates)
    so `cmd_init` can use this to CREATE DATABASE wolf before
    connecting to it.
    """
    cmd = [
        str(binaries.psql),
        "-h", str(layout.socket_dir),
        "-p", str(port),
        "-d", dbname,
        "-v", "ON_ERROR_STOP=1",
        "-c", sql,
    ]
    if user is not None:
        cmd.extend(["-U", user])
    result = subprocess.run(cmd, check=False)  # noqa: S603
    if result.returncode != 0:
        raise WolfDatabaseError(
            f"psql command failed (exit {result.returncode}): {sql[:80]}",
        )


def is_pgvector_installed(
    binaries: PostgresBinaries,
    layout: DatabaseLayout,
    *,
    port: int = 5432,
) -> bool:
    """Check whether the postgresql-17-pgvector package is available.

    Queries `pg_available_extensions` for 'vector'. The cluster must
    be running for this to work — typically called between `start`
    and the `CREATE EXTENSION vector` step in `init`. `port` must
    match the cluster's configured port; the socket filename embeds
    the port so a mismatched value silently fails.
    """
    cmd = [
        str(binaries.psql),
        "-h", str(layout.socket_dir),
        "-p", str(port),
        "-d", "postgres",
        "-tA",  # tuples-only, unaligned — clean output for parsing
        "-c", "SELECT 1 FROM pg_available_extensions WHERE name = 'vector';",
    ]
    result = subprocess.run(  # noqa: S603
        cmd, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return False
    return result.stdout.strip() == "1"


# ─── one-shot "is the data dir initialized" check ────────────────────────


def data_dir_is_initialized(layout: DatabaseLayout) -> bool:
    """True iff `layout.data_dir` looks like an initdb'd cluster.

    Postgres marks an initialized cluster by writing `PG_VERSION` to
    the data dir. Checking for that file is the canonical "has
    initdb run here?" check; less brittle than looking for postgresql.conf
    (which we override anyway) or postmaster.pid (only present
    when running).
    """
    return (layout.data_dir / "PG_VERSION").is_file()


