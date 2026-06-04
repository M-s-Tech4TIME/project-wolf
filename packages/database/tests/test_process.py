"""Tests for wolf_database.process — subprocess wrappers + helpers.

These tests don't run real Postgres — they verify the helpers
issue the right subprocess calls, parse outputs correctly, and
surface errors with the right exception type. Integration testing
against a real Postgres lands in slice 5.7-c's dev workflow gate.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from wolf_database.binaries import PostgresBinaries
from wolf_database.layout import DatabaseLayout
from wolf_database.process import (
    PgCtlStatus,
    WolfDatabaseError,
    _parse_pid,
    data_dir_is_initialized,
    is_pgvector_installed,
    run_initdb,
    run_pg_ctl_start,
    run_pg_ctl_status,
    run_pg_ctl_stop,
    run_psql_command,
)


@pytest.fixture
def layout(tmp_path: Path) -> DatabaseLayout:
    return DatabaseLayout(
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "cfg",
        socket_dir=tmp_path / "sock",
    )


@pytest.fixture
def fake_binaries(tmp_path: Path) -> PostgresBinaries:
    """Fake binary paths — they don't have to exist, since we mock subprocess.run."""
    return PostgresBinaries(
        pg_ctl=tmp_path / "pg_ctl",
        initdb=tmp_path / "initdb",
        psql=tmp_path / "psql",
        postgres=tmp_path / "postgres",
    )


def _mock_completed(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout.encode(), stderr=b"",
    )


# ─── data_dir_is_initialized ────────────────────────────────────────────


def test_data_dir_is_initialized_false_for_empty_dir(layout: DatabaseLayout) -> None:
    layout.data_dir.mkdir(parents=True)
    assert data_dir_is_initialized(layout) is False


def test_data_dir_is_initialized_true_when_pg_version_present(
    layout: DatabaseLayout,
) -> None:
    layout.data_dir.mkdir(parents=True)
    (layout.data_dir / "PG_VERSION").write_text("17\n")
    assert data_dir_is_initialized(layout) is True


def test_data_dir_is_initialized_false_when_data_dir_missing(
    layout: DatabaseLayout,
) -> None:
    # Don't create layout.data_dir at all
    assert data_dir_is_initialized(layout) is False


# ─── _parse_pid ─────────────────────────────────────────────────────────


def test_parse_pid_extracts_integer() -> None:
    assert _parse_pid("pg_ctl: server is running (PID: 12345)") == 12345


def test_parse_pid_returns_none_when_absent() -> None:
    assert _parse_pid("pg_ctl: server is not running") is None


# ─── run_initdb ─────────────────────────────────────────────────────────


def test_run_initdb_creates_parent_dir_and_invokes_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    layout: DatabaseLayout,
    fake_binaries: PostgresBinaries,
) -> None:
    invocations: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        invocations.append(cmd)
        return _mock_completed(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    run_initdb(fake_binaries, layout)
    # data_dir created
    assert layout.data_dir.is_dir()
    # initdb called once with the data dir + auth method
    assert len(invocations) == 1
    cmd = invocations[0]
    assert str(fake_binaries.initdb) == cmd[0]
    assert "--pgdata" in cmd
    assert str(layout.data_dir) in cmd
    assert "--auth-host" in cmd


def test_run_initdb_raises_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
    layout: DatabaseLayout,
    fake_binaries: PostgresBinaries,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_, **__: _mock_completed(returncode=1),
    )
    with pytest.raises(WolfDatabaseError, match="initdb"):
        run_initdb(fake_binaries, layout)


# ─── run_pg_ctl_start / stop ────────────────────────────────────────────


def test_run_pg_ctl_start_passes_config_file_flag(
    monkeypatch: pytest.MonkeyPatch,
    layout: DatabaseLayout,
    fake_binaries: PostgresBinaries,
) -> None:
    """The launcher always points pg_ctl at our wolf-database-owned conf,
    not the one initdb dropped in the data dir."""
    captured: list[str] = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **_: captured.extend(cmd) or _mock_completed(0),
    )
    run_pg_ctl_start(fake_binaries, layout, wait=True)
    joined = " ".join(captured)
    assert "start" in captured
    assert f"--config-file={layout.postgresql_conf_path}" in joined
    assert "-w" in captured  # wait flag


def test_run_pg_ctl_start_no_wait_uses_capital_w(
    monkeypatch: pytest.MonkeyPatch,
    layout: DatabaseLayout,
    fake_binaries: PostgresBinaries,
) -> None:
    captured: list[str] = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **_: captured.extend(cmd) or _mock_completed(0),
    )
    run_pg_ctl_start(fake_binaries, layout, wait=False)
    assert "-W" in captured


def test_run_pg_ctl_stop_passes_mode(
    monkeypatch: pytest.MonkeyPatch,
    layout: DatabaseLayout,
    fake_binaries: PostgresBinaries,
) -> None:
    captured: list[str] = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **_: captured.extend(cmd) or _mock_completed(0),
    )
    run_pg_ctl_stop(fake_binaries, layout, mode="immediate")
    assert "-m" in captured
    assert "immediate" in captured


def test_run_pg_ctl_stop_raises_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    layout: DatabaseLayout,
    fake_binaries: PostgresBinaries,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_, **__: _mock_completed(returncode=1),
    )
    with pytest.raises(WolfDatabaseError, match="stop"):
        run_pg_ctl_stop(fake_binaries, layout)


# ─── run_pg_ctl_status ──────────────────────────────────────────────────


def test_run_pg_ctl_status_returns_running_with_pid(
    monkeypatch: pytest.MonkeyPatch,
    layout: DatabaseLayout,
    fake_binaries: PostgresBinaries,
) -> None:
    layout.data_dir.mkdir(parents=True)

    def fake_run(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="pg_ctl: server is running (PID: 9876)\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    status = run_pg_ctl_status(fake_binaries, layout)
    assert status == PgCtlStatus(running=True, pid=9876, data_dir_ok=True)


def test_run_pg_ctl_status_returns_stopped_on_exit_3(
    monkeypatch: pytest.MonkeyPatch,
    layout: DatabaseLayout,
    fake_binaries: PostgresBinaries,
) -> None:
    layout.data_dir.mkdir(parents=True)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_, **__: subprocess.CompletedProcess(
            args=[], returncode=3, stdout="", stderr="",
        ),
    )
    status = run_pg_ctl_status(fake_binaries, layout)
    assert status == PgCtlStatus(running=False, pid=None, data_dir_ok=True)


def test_run_pg_ctl_status_returns_data_dir_missing_when_dir_absent(
    layout: DatabaseLayout,
    fake_binaries: PostgresBinaries,
) -> None:
    """Short-circuits without invoking pg_ctl when data dir is gone."""
    assert not layout.data_dir.exists()
    status = run_pg_ctl_status(fake_binaries, layout)
    assert status == PgCtlStatus(running=False, pid=None, data_dir_ok=False)


def test_run_pg_ctl_status_returns_data_dir_bad_on_exit_4(
    monkeypatch: pytest.MonkeyPatch,
    layout: DatabaseLayout,
    fake_binaries: PostgresBinaries,
) -> None:
    layout.data_dir.mkdir(parents=True)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_, **__: subprocess.CompletedProcess(
            args=[], returncode=4, stdout="", stderr="",
        ),
    )
    status = run_pg_ctl_status(fake_binaries, layout)
    assert status.running is False
    assert status.data_dir_ok is False


# ─── run_psql_command ───────────────────────────────────────────────────


def test_run_psql_command_uses_socket_host_and_on_error_stop(
    monkeypatch: pytest.MonkeyPatch,
    layout: DatabaseLayout,
    fake_binaries: PostgresBinaries,
) -> None:
    captured: list[str] = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **_: captured.extend(cmd) or _mock_completed(0),
    )
    run_psql_command(fake_binaries, layout, sql="SELECT 1;")
    # Socket-dir host, ON_ERROR_STOP=1, command flag
    assert "-h" in captured
    assert str(layout.socket_dir) in captured
    assert "ON_ERROR_STOP=1" in captured
    assert "SELECT 1;" in captured


def test_run_psql_command_raises_on_psql_error(
    monkeypatch: pytest.MonkeyPatch,
    layout: DatabaseLayout,
    fake_binaries: PostgresBinaries,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_, **__: _mock_completed(returncode=1),
    )
    with pytest.raises(WolfDatabaseError, match="psql"):
        run_psql_command(fake_binaries, layout, sql="SELECT 1;")


def test_run_psql_command_targets_named_db(
    monkeypatch: pytest.MonkeyPatch,
    layout: DatabaseLayout,
    fake_binaries: PostgresBinaries,
) -> None:
    """The dbname kwarg is passed via -d so we connect to the right DB."""
    captured: list[str] = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **_: captured.extend(cmd) or _mock_completed(0),
    )
    run_psql_command(fake_binaries, layout, sql="SELECT 1;", dbname="wolf")
    assert "-d" in captured
    assert "wolf" in captured


# ─── is_pgvector_installed ──────────────────────────────────────────────


def test_is_pgvector_installed_returns_true_when_psql_prints_1(
    monkeypatch: pytest.MonkeyPatch,
    layout: DatabaseLayout,
    fake_binaries: PostgresBinaries,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_, **__: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="1\n", stderr="",
        ),
    )
    assert is_pgvector_installed(fake_binaries, layout) is True


def test_is_pgvector_installed_returns_false_when_psql_prints_empty(
    monkeypatch: pytest.MonkeyPatch,
    layout: DatabaseLayout,
    fake_binaries: PostgresBinaries,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_, **__: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        ),
    )
    assert is_pgvector_installed(fake_binaries, layout) is False


def test_is_pgvector_installed_returns_false_on_psql_error(
    monkeypatch: pytest.MonkeyPatch,
    layout: DatabaseLayout,
    fake_binaries: PostgresBinaries,
) -> None:
    """A non-zero psql exit (Postgres not running) → false, not raise."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_, **__: subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error",
        ),
    )
    assert is_pgvector_installed(fake_binaries, layout) is False
