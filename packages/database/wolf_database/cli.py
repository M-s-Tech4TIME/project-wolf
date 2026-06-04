"""wolf-database CLI — operator-facing lifecycle for the bundled Postgres.

Five subcommands, parallel to wolf-cert's shape:

* `init`        — run initdb, write the config templates, create
                  the wolf role + db, install pgvector. One-time
                  setup; refuses to clobber an existing data dir.
* `start`       — start Postgres against the wolf-database layout.
* `stop`        — stop Postgres.
* `status`      — report running state + PID + layout paths.
* `reconfigure` — rewrite the config templates in place without
                  re-initdb. Use after changing env var overrides
                  for listen-address, port, etc. Operator restarts
                  Postgres themselves to apply.

Every subcommand resolves the layout via the same
`resolve_layout()` the substrate uses, so env-var overrides
behave identically across them. Operator can point at a non-
default install with one set of env vars and all subcommands obey.

The CLI never reads or stores passwords on disk. `init` generates
a random password, prints it once, and tells the operator to copy
it into wolf-server's `.env`. wolf-database itself doesn't need
the password after creation — local connections from the same OS
user use the Postgres `peer` auth path automatically.
"""

from __future__ import annotations

import argparse
import secrets
import sys
import time
from enum import IntEnum

from wolf_database.binaries import (
    PostgresBinaryNotFoundError,
    find_postgres_binaries,
    verify_postgres_supported,
)
from wolf_database.config import DEFAULT_PORT, PostgresqlConfOptions, write_config
from wolf_database.layout import DB_NAME_DEFAULT, DB_USER_DEFAULT, resolve_layout
from wolf_database.process import (
    WolfDatabaseError,
    data_dir_is_initialized,
    is_pgvector_installed,
    run_initdb,
    run_pg_ctl_start,
    run_pg_ctl_status,
    run_pg_ctl_stop,
    run_psql_command,
)


class _ExitCode(IntEnum):
    OK = 0
    USER_ERROR = 2
    REFUSED = 3
    BINARY_MISSING = 4


# ─── init ────────────────────────────────────────────────────────────────


def cmd_init(args: argparse.Namespace) -> int:
    """Run the full one-shot setup. Refuses if data dir is already initialized."""
    layout = resolve_layout(production=args.production)

    try:
        binaries = find_postgres_binaries()
    except PostgresBinaryNotFoundError as exc:
        _eprint(str(exc))
        return _ExitCode.BINARY_MISSING

    try:
        verify_postgres_supported(binaries)
    except RuntimeError as exc:
        _eprint(str(exc))
        return _ExitCode.BINARY_MISSING

    if data_dir_is_initialized(layout):
        _eprint(
            f"refusing to init: data dir {layout.data_dir} already "
            "contains a PG_VERSION file (already initialized).\n"
            "  Use `wolf-database start` to launch it, or "
            "`wolf-database destroy --yes` then re-init for a fresh "
            "cluster.",
        )
        return _ExitCode.REFUSED

    print(f"→ initdb on {layout.data_dir}")  # noqa: T201
    try:
        run_initdb(binaries, layout)
    except WolfDatabaseError as exc:
        _eprint(str(exc))
        return _ExitCode.USER_ERROR

    print(f"→ writing config to {layout.config_dir}")  # noqa: T201
    write_config(layout, pg_options=PostgresqlConfOptions(port=args.port))

    # Ensure the socket dir exists with permissions Postgres can
    # write to it. Created mode 0755 — the socket itself gets
    # 0777 by Postgres convention so any local user can connect
    # (auth is enforced at the role layer, not filesystem ACL).
    layout.socket_dir.mkdir(parents=True, exist_ok=True)

    print("→ starting Postgres (waiting for ready)")  # noqa: T201
    try:
        run_pg_ctl_start(binaries, layout, wait=True)
    except WolfDatabaseError as exc:
        _eprint(str(exc))
        _eprint(
            "Hint: check that the data dir's pg_hba.conf permits local "
            "connections; also confirm the user running `wolf-database "
            "init` matches the OS user initdb made superuser.",
        )
        return _ExitCode.USER_ERROR

    try:
        # Sanity: pgvector must be available before we try CREATE EXTENSION.
        # Postgres can take a beat after pg_ctl reports ready before
        # responding to psql; one retry to be safe. The `finally`
        # block below handles the stop on this error path too —
        # don't call stop here or we double-stop.
        if not _await_pgvector(binaries, layout, attempts=5):
            _eprint(
                "wolf-database requires the pgvector extension. The "
                f"running Postgres at {layout.socket_dir} reports it is "
                "NOT available.\n"
                "  Install: `apt install postgresql-17-pgvector` "
                "(Debian/Ubuntu) or `dnf install pgvector_17` "
                "(RHEL/Fedora).\n"
                "  Then re-run `wolf-database init`.",
            )
            return _ExitCode.USER_ERROR

        password = secrets.token_urlsafe(24)
        print(f"→ creating role '{DB_USER_DEFAULT}'")  # noqa: T201
        # WARNING: the password is embedded in the CREATE ROLE
        # statement we pass to psql. psql echoes the statement
        # if -e is passed; we don't, so the password isn't logged.
        # Belt-and-braces: scram-sha-256 means the password isn't
        # stored in plaintext server-side either.
        run_psql_command(
            binaries, layout,
            sql=(
                f"CREATE ROLE {DB_USER_DEFAULT} WITH LOGIN PASSWORD "
                f"'{password}';"
            ),
        )

        print(f"→ creating database '{DB_NAME_DEFAULT}'")  # noqa: T201
        run_psql_command(
            binaries, layout,
            sql=(
                f"CREATE DATABASE {DB_NAME_DEFAULT} OWNER {DB_USER_DEFAULT};"
            ),
        )

        print(f"→ installing pgvector in '{DB_NAME_DEFAULT}'")  # noqa: T201
        run_psql_command(
            binaries, layout,
            dbname=DB_NAME_DEFAULT,
            sql="CREATE EXTENSION vector;",
        )

    finally:
        print("→ stopping Postgres")  # noqa: T201
        try:
            run_pg_ctl_stop(binaries, layout)
        except WolfDatabaseError as exc:
            _eprint(f"warning: stop failed after init: {exc}")

    print("")  # noqa: T201
    print(f"✓ wolf-database initialized at {layout.data_dir}")  # noqa: T201
    print("")  # noqa: T201
    print("Copy this into your .env (or `services/dashboard/.env.local`):")  # noqa: T201
    print("")  # noqa: T201
    print(  # noqa: T201
        f"  DATABASE_URL=postgresql+asyncpg://{DB_USER_DEFAULT}:"
        f"{password}@localhost:{args.port}/{DB_NAME_DEFAULT}",
    )
    print("")  # noqa: T201
    print("Then run `wolf-database start` to launch.")  # noqa: T201
    return _ExitCode.OK


def _await_pgvector(
    binaries: object,
    layout: object,
    *,
    attempts: int = 5,
    delay_s: float = 0.5,
) -> bool:
    """Poll `pg_available_extensions` for 'vector' across a few retries.

    pg_ctl's `-w` makes start block until Postgres is "accepting
    connections," but in practice the postmaster's shared memory
    isn't fully populated for a moment — pg_available_extensions
    can return zero rows on the first call. A handful of retries
    covers it.
    """
    for _ in range(attempts):
        if is_pgvector_installed(binaries, layout):  # type: ignore[arg-type]
            return True
        time.sleep(delay_s)
    return False


# ─── start / stop / status / reconfigure ─────────────────────────────────


def cmd_start(args: argparse.Namespace) -> int:
    layout = resolve_layout(production=args.production)
    binaries = find_postgres_binaries()

    if not data_dir_is_initialized(layout):
        _eprint(
            f"refusing to start: data dir {layout.data_dir} not "
            "initialized. Run `wolf-database init` first.",
        )
        return _ExitCode.REFUSED

    try:
        run_pg_ctl_start(binaries, layout, wait=True)
    except WolfDatabaseError as exc:
        _eprint(str(exc))
        return _ExitCode.USER_ERROR

    print(  # noqa: T201
        f"✓ wolf-database started at {layout.data_dir} "
        f"(socket: {layout.socket_dir})",
    )
    return _ExitCode.OK


def cmd_stop(args: argparse.Namespace) -> int:
    layout = resolve_layout(production=args.production)
    binaries = find_postgres_binaries()
    try:
        run_pg_ctl_stop(binaries, layout, mode=args.mode)
    except WolfDatabaseError as exc:
        _eprint(str(exc))
        return _ExitCode.USER_ERROR
    print(f"✓ wolf-database stopped (mode={args.mode})")  # noqa: T201
    return _ExitCode.OK


def cmd_status(args: argparse.Namespace) -> int:
    layout = resolve_layout(production=args.production)
    try:
        binaries = find_postgres_binaries()
    except PostgresBinaryNotFoundError as exc:
        _eprint(str(exc))
        return _ExitCode.BINARY_MISSING

    status = run_pg_ctl_status(binaries, layout)

    print(f"data dir:   {layout.data_dir}")  # noqa: T201
    print(f"config dir: {layout.config_dir}")  # noqa: T201
    print(f"socket dir: {layout.socket_dir}")  # noqa: T201
    if not status.data_dir_ok:
        print("state:      DATA DIR MISSING — run `wolf-database init`.")  # noqa: T201
    elif status.running:
        print(f"state:      RUNNING (PID {status.pid})")  # noqa: T201
    else:
        print("state:      STOPPED — run `wolf-database start`.")  # noqa: T201
    return _ExitCode.OK


def cmd_reconfigure(args: argparse.Namespace) -> int:
    """Rewrite postgresql.conf + pg_hba.conf in place. Doesn't restart Postgres."""
    layout = resolve_layout(production=args.production)
    write_config(layout)
    print(  # noqa: T201
        f"✓ config rewritten at {layout.config_dir}\n"
        "  Restart Postgres to apply: "
        "`wolf-database stop && wolf-database start`",
    )
    return _ExitCode.OK


# ─── argparse dispatch ───────────────────────────────────────────────────


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)  # noqa: T201


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wolf-database",
        description=(
            "Manage Wolf's bundled Postgres component. Phase 5.7 of the "
            "build roadmap — see docs/decisions/0016 for the architecture."
        ),
    )
    p.add_argument(
        "--production",
        action="store_true",
        default=None,
        help=(
            "Use the production filesystem layout (/var/lib/wolf-database/"
            "...). Default is dev (<repo>/.local/wolf-database/...). "
            "Operators with a custom layout set the WOLF_DATABASE_*_DIR "
            "env vars instead."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser(
        "init",
        help="One-shot setup: initdb, write config, create role + db, install pgvector",
    )
    p_init.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=(
            f"TCP port wolf-database listens on (default: {DEFAULT_PORT}). "
            "Override when a system Postgres or another wolf-database is "
            "already on the default port."
        ),
    )
    p_init.set_defaults(func=cmd_init)

    p_start = sub.add_parser("start", help="Start Postgres")
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="Stop Postgres")
    p_stop.add_argument(
        "--mode",
        choices=["smart", "fast", "immediate"],
        default="fast",
        help="pg_ctl shutdown mode (default: fast)",
    )
    p_stop.set_defaults(func=cmd_stop)

    p_status = sub.add_parser(
        "status",
        help="Show running state + resolved layout",
    )
    p_status.set_defaults(func=cmd_status)

    p_reconfigure = sub.add_parser(
        "reconfigure",
        help="Rewrite the config templates from env (doesn't restart Postgres)",
    )
    p_reconfigure.set_defaults(func=cmd_reconfigure)

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
