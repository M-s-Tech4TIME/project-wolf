"""Tests for wolf_database.cli — argparse dispatch + subcommand logic.

The init flow's full integration (real initdb + real pg_ctl + real
psql) is exercised by the live smoke in slice 5.7-c. These tests
cover the dispatch wiring, the refusal paths (already-initialized
data dir, missing binaries, missing data dir on start), and the
status-reporting branch logic. Subprocess invocations are mocked.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from wolf_database.binaries import PostgresBinaries, PostgresBinaryNotFoundError
from wolf_database.cli import _build_parser, _ExitCode, main
from wolf_database.process import PgCtlStatus


@pytest.fixture(autouse=True)
def _isolated_layout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    """Point every wolf-database path env var at tmp_path.

    Every CLI command resolves the layout, so giving each test a
    pristine layout under tmp_path means tests can't pollute each
    other or the real .local/wolf-database.
    """
    monkeypatch.setenv("WOLF_DATABASE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WOLF_DATABASE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("WOLF_DATABASE_SOCKET_DIR", str(tmp_path / "sock"))
    monkeypatch.delenv("WOLF_DATABASE_PRODUCTION", raising=False)
    return tmp_path


def _fake_binaries(tmp_path: Path) -> PostgresBinaries:
    return PostgresBinaries(
        pg_ctl=tmp_path / "pg_ctl",
        initdb=tmp_path / "initdb",
        psql=tmp_path / "psql",
        postgres=tmp_path / "postgres",
    )


# ─── argparse dispatch ──────────────────────────────────────────────────


def test_parser_requires_subcommand() -> None:
    p = _build_parser()
    with pytest.raises(SystemExit):
        p.parse_args([])


def test_parser_accepts_all_five_subcommands() -> None:
    p = _build_parser()
    for sub in ("init", "start", "stop", "status", "reconfigure"):
        args = p.parse_args([sub])
        assert args.cmd == sub


def test_parser_stop_mode_defaults_to_fast() -> None:
    args = _build_parser().parse_args(["stop"])
    assert args.mode == "fast"


def test_parser_stop_mode_override() -> None:
    args = _build_parser().parse_args(["stop", "--mode", "immediate"])
    assert args.mode == "immediate"


# ─── status ──────────────────────────────────────────────────────────────


def test_status_reports_data_dir_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    _isolated_layout: Path,
) -> None:
    """When the data dir doesn't exist, status says so + suggests init."""
    monkeypatch.setattr(
        "wolf_database.cli.find_postgres_binaries",
        lambda: _fake_binaries(_isolated_layout),
    )
    # Don't create the data dir.
    rc = main(["status"])
    assert rc == _ExitCode.OK
    out = capsys.readouterr().out
    assert "DATA DIR MISSING" in out
    assert "wolf-database init" in out


def test_status_reports_running_with_pid(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    _isolated_layout: Path,
) -> None:
    """RUNNING + PID branch."""
    (_isolated_layout / "data").mkdir(parents=True)
    monkeypatch.setattr(
        "wolf_database.cli.find_postgres_binaries",
        lambda: _fake_binaries(_isolated_layout),
    )
    monkeypatch.setattr(
        "wolf_database.cli.run_pg_ctl_status",
        lambda *_: PgCtlStatus(running=True, pid=12345, data_dir_ok=True),
    )
    rc = main(["status"])
    assert rc == _ExitCode.OK
    out = capsys.readouterr().out
    assert "RUNNING" in out
    assert "12345" in out


def test_status_reports_stopped(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    _isolated_layout: Path,
) -> None:
    (_isolated_layout / "data").mkdir(parents=True)
    monkeypatch.setattr(
        "wolf_database.cli.find_postgres_binaries",
        lambda: _fake_binaries(_isolated_layout),
    )
    monkeypatch.setattr(
        "wolf_database.cli.run_pg_ctl_status",
        lambda *_: PgCtlStatus(running=False, pid=None, data_dir_ok=True),
    )
    rc = main(["status"])
    assert rc == _ExitCode.OK
    out = capsys.readouterr().out
    assert "STOPPED" in out


def test_status_returns_binary_missing_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When pg_ctl isn't on PATH, status reports + exits cleanly."""

    def raiser() -> None:
        raise PostgresBinaryNotFoundError(tool="pg_ctl", searched=[])

    monkeypatch.setattr("wolf_database.cli.find_postgres_binaries", raiser)
    rc = main(["status"])
    assert rc == _ExitCode.BINARY_MISSING
    err = capsys.readouterr().err
    assert "pg_ctl" in err


# ─── init refusal paths ─────────────────────────────────────────────────


def test_init_refuses_when_data_dir_already_initialized(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    _isolated_layout: Path,
) -> None:
    """Refuse with REFUSED exit code if PG_VERSION exists."""
    data_dir = _isolated_layout / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "PG_VERSION").write_text("17\n")

    monkeypatch.setattr(
        "wolf_database.cli.find_postgres_binaries",
        lambda: _fake_binaries(_isolated_layout),
    )
    monkeypatch.setattr("wolf_database.cli.verify_postgres_supported", lambda _: None)

    rc = main(["init"])
    assert rc == _ExitCode.REFUSED
    err = capsys.readouterr().err
    assert "already" in err.lower()


def test_init_returns_binary_missing_when_no_postgres(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def raiser() -> None:
        raise PostgresBinaryNotFoundError(tool="initdb", searched=[])

    monkeypatch.setattr("wolf_database.cli.find_postgres_binaries", raiser)
    rc = main(["init"])
    assert rc == _ExitCode.BINARY_MISSING
    err = capsys.readouterr().err
    assert "initdb" in err


# ─── start refusal path ─────────────────────────────────────────────────


def test_start_refuses_when_not_initialized(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    _isolated_layout: Path,
) -> None:
    """Refuse to `start` against an empty/missing data dir."""
    monkeypatch.setattr(
        "wolf_database.cli.find_postgres_binaries",
        lambda: _fake_binaries(_isolated_layout),
    )
    rc = main(["start"])
    assert rc == _ExitCode.REFUSED
    err = capsys.readouterr().err
    assert "init" in err.lower()


# ─── reconfigure ────────────────────────────────────────────────────────


def test_reconfigure_writes_config_without_starting_postgres(
    capsys: pytest.CaptureFixture[str],
    _isolated_layout: Path,
) -> None:
    """`reconfigure` regenerates conf files but doesn't touch pg_ctl."""
    rc = main(["reconfigure"])
    assert rc == _ExitCode.OK
    out = capsys.readouterr().out
    assert "config rewritten" in out
    # The actual files exist.
    assert (_isolated_layout / "cfg" / "postgresql.conf").exists()
    assert (_isolated_layout / "cfg" / "pg_hba.conf").exists()


def test_reconfigure_preserves_existing_port_when_no_flag(
    _isolated_layout: Path,
) -> None:
    """Regression: a cluster init'd with --port 17860 must NOT have its
    postgresql.conf silently rewritten to port=5432 by a later
    reconfigure. The reconfigure command reads the existing
    postgresql.conf and preserves the port unless --port overrides."""
    # Lay down a pretend "init'd" config with a non-default port.
    cfg = _isolated_layout / "cfg"
    cfg.mkdir(parents=True)
    (cfg / "postgresql.conf").write_text(
        "data_directory = '/tmp/x'\nport = 17860\nlisten_addresses = 'localhost'\n",
    )

    rc = main(["reconfigure"])
    assert rc == _ExitCode.OK

    # The rewritten conf still has port = 17860 (not 5432).
    new_conf = (cfg / "postgresql.conf").read_text()
    assert "port = 17860" in new_conf
    assert "port = 5432" not in new_conf


def test_reconfigure_explicit_port_overrides_existing(
    _isolated_layout: Path,
) -> None:
    """Passing --port to reconfigure overrides whatever the existing
    conf had — operator-driven port change."""
    cfg = _isolated_layout / "cfg"
    cfg.mkdir(parents=True)
    (cfg / "postgresql.conf").write_text("port = 17860\n")

    rc = main(["reconfigure", "--port", "25432"])
    assert rc == _ExitCode.OK

    new_conf = (cfg / "postgresql.conf").read_text()
    assert "port = 25432" in new_conf
    assert "port = 17860" not in new_conf


def test_reconfigure_defaults_to_5432_when_no_existing_conf(
    _isolated_layout: Path,
) -> None:
    """First-time reconfigure (no existing postgresql.conf) falls back
    to the documented default port."""
    rc = main(["reconfigure"])
    assert rc == _ExitCode.OK
    new_conf = (_isolated_layout / "cfg" / "postgresql.conf").read_text()
    assert "port = 5432" in new_conf
