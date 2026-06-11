"""Tests for wolf_database.binaries — discovery + version detection."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from wolf_database import binaries as binaries_module
from wolf_database.binaries import (
    REQUIRED_MAJOR_VERSION,
    PostgresBinaries,
    PostgresBinaryNotFoundError,
    _find_one,
    find_postgres_binaries,
    postgres_major_version,
    verify_postgres_supported,
)


@pytest.fixture(autouse=True)
def _no_known_bin_dirs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out the distro-known Postgres directories.

    The CI runner + dev hosts may have a real Postgres installed
    under /usr/lib/postgresql/17/bin or /usr/pgsql-17/bin, which
    would short-circuit the discovery before the test fixture PATH
    can take effect. Make every test see an empty distro list so
    only env-override + PATH matter. The tests that EXPLICITLY
    want to verify distro-dir discovery should set this fixture's
    return manually.
    """
    monkeypatch.setattr(binaries_module, "_KNOWN_BIN_DIRS", ())


def _make_fake_executable(path: Path, body: str = "") -> Path:
    """Write a real executable file at `path` (parent dirs created).

    Used to simulate `pg_ctl` etc. without depending on the host
    actually having Postgres installed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


# ─── _find_one ─────────────────────────────────────────────────────────────


def test_find_one_uses_env_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake = _make_fake_executable(tmp_path / "my_pg_ctl")
    monkeypatch.setenv("WOLF_DATABASE_PG_CTL", str(fake))
    # Clear PATH so the env-override is the only thing that can resolve.
    monkeypatch.setenv("PATH", "")

    found = _find_one("pg_ctl")
    assert found == fake.resolve()


def test_find_one_falls_through_to_path_when_no_known_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No env override + no /usr/lib/postgresql/17/... → uses PATH."""
    fake = _make_fake_executable(tmp_path / "psql")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("WOLF_DATABASE_PSQL", raising=False)

    found = _find_one("psql")
    assert found == fake.resolve()


def test_find_one_raises_when_nothing_resolves(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Nothing on PATH, no env override → PostgresBinaryNotFoundError."""
    monkeypatch.setenv("PATH", str(tmp_path))  # empty dir
    monkeypatch.delenv("WOLF_DATABASE_PG_CTL", raising=False)

    with pytest.raises(PostgresBinaryNotFoundError) as exc:
        _find_one("pg_ctl")

    assert "pg_ctl" in str(exc.value)
    # The error names the install hint so operators see a fix path.
    assert "postgresql-17" in str(exc.value)


def test_find_one_env_override_pointing_at_nonexistent_path_still_searches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A bad env override doesn't short-circuit — we keep looking at
    distro paths + PATH. Operators with a stale env value still get a
    working tool if it's on PATH."""
    monkeypatch.setenv("WOLF_DATABASE_INITDB", "/nonexistent/initdb")
    fallback = _make_fake_executable(tmp_path / "initdb")
    monkeypatch.setenv("PATH", str(tmp_path))

    found = _find_one("initdb")
    assert found == fallback.resolve()


# ─── postgres_major_version ────────────────────────────────────────────────


def test_postgres_major_version_parses_real_output(
    tmp_path: Path,
) -> None:
    """Parse a realistic `postgres --version` line."""
    fake = _make_fake_executable(
        tmp_path / "postgres",
        body='echo "postgres (PostgreSQL) 17.1 (Ubuntu 17.1-1.pgdg22.04+1)"',
    )
    assert postgres_major_version(fake) == 17


def test_postgres_major_version_parses_x_dot_y_dot_z(tmp_path: Path) -> None:
    fake = _make_fake_executable(
        tmp_path / "postgres",
        body='echo "postgres (PostgreSQL) 16.3"',
    )
    assert postgres_major_version(fake) == 16


def test_postgres_major_version_raises_on_unexpected_output(
    tmp_path: Path,
) -> None:
    fake = _make_fake_executable(
        tmp_path / "postgres",
        body='echo "not the expected output"',
    )
    with pytest.raises(ValueError, match="unexpected"):
        postgres_major_version(fake)


# ─── verify_postgres_supported ─────────────────────────────────────────────


def test_verify_postgres_supported_passes_for_correct_major(
    tmp_path: Path,
) -> None:
    """A Postgres 17.x install passes the version gate."""
    pg = _make_fake_executable(
        tmp_path / "postgres",
        body=f'echo "postgres (PostgreSQL) {REQUIRED_MAJOR_VERSION}.0"',
    )
    bins = PostgresBinaries(pg_ctl=pg, initdb=pg, psql=pg, postgres=pg)
    # No exception = pass.
    verify_postgres_supported(bins)


def test_verify_postgres_supported_rejects_older_major(
    tmp_path: Path,
) -> None:
    """A Postgres 15.x install is rejected with a clear error."""
    pg = _make_fake_executable(
        tmp_path / "postgres",
        body='echo "postgres (PostgreSQL) 15.4"',
    )
    bins = PostgresBinaries(pg_ctl=pg, initdb=pg, psql=pg, postgres=pg)
    with pytest.raises(RuntimeError, match=f"requires PostgreSQL {REQUIRED_MAJOR_VERSION}+"):
        verify_postgres_supported(bins)


# ─── find_postgres_binaries (integration of the above) ────────────────────


def test_find_postgres_binaries_returns_all_four(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When all four binaries exist on PATH, the helper returns them."""
    for tool in ("pg_ctl", "initdb", "psql", "postgres"):
        _make_fake_executable(tmp_path / tool)
        monkeypatch.delenv(f"WOLF_DATABASE_{tool.upper()}", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))

    bins = find_postgres_binaries()
    assert bins.pg_ctl.parent == tmp_path
    assert bins.initdb.parent == tmp_path
    assert bins.psql.parent == tmp_path
    assert bins.postgres.parent == tmp_path
