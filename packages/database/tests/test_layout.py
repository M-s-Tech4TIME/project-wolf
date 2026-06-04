"""Tests for wolf_database.layout — path resolution + env overrides."""

from __future__ import annotations

from pathlib import Path

import pytest
from wolf_database.layout import (
    DB_NAME_DEFAULT,
    DB_USER_DEFAULT,
    DatabaseLayout,
    resolve_layout,
)


def test_db_name_and_user_defaults_match_existing_dev_config() -> None:
    """The DB name + user constants must match what wolf-server's
    existing `.env` / `.env.example` expects (db: 'wolf', user: 'wolf').
    A drift here would silently break every fresh-clone setup."""
    assert DB_NAME_DEFAULT == "wolf"
    assert DB_USER_DEFAULT == "wolf"


def test_dev_layout_lives_under_repo_dotlocal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default dev layout puts everything under <repo>/.local/wolf-database/."""
    # Clear any env overrides that might be leaking from the operator's
    # shell so the test sees the pristine defaults.
    for var in (
        "WOLF_DATABASE_DATA_DIR",
        "WOLF_DATABASE_CONFIG_DIR",
        "WOLF_DATABASE_SOCKET_DIR",
        "WOLF_DATABASE_PRODUCTION",
    ):
        monkeypatch.delenv(var, raising=False)

    layout = resolve_layout(production=False)
    assert ".local/wolf-database/data" in str(layout.data_dir)
    assert ".local/wolf-database/config" in str(layout.config_dir)
    assert ".local/wolf-database/socket" in str(layout.socket_dir)


def test_production_layout_lives_under_var_lib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production layout uses the FHS paths from ADR 0016."""
    for var in (
        "WOLF_DATABASE_DATA_DIR",
        "WOLF_DATABASE_CONFIG_DIR",
        "WOLF_DATABASE_SOCKET_DIR",
    ):
        monkeypatch.delenv(var, raising=False)

    layout = resolve_layout(production=True)
    # `resolve()` because on Linux `/var/run` is a symlink to `/run`, so
    # the resolved form differs from the literal default. We compare
    # canonical-to-canonical.
    assert layout.data_dir == Path("/var/lib/wolf-database/data").resolve()
    assert layout.config_dir == Path("/etc/wolf-database").resolve()
    assert layout.socket_dir == Path("/var/run/wolf-database").resolve()


def test_env_var_overrides_each_path_independently(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Each env var overrides ONLY its own dir; others fall back to default."""
    data_override = tmp_path / "custom-data"
    monkeypatch.setenv("WOLF_DATABASE_DATA_DIR", str(data_override))
    monkeypatch.delenv("WOLF_DATABASE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("WOLF_DATABASE_SOCKET_DIR", raising=False)

    layout = resolve_layout(production=True)
    assert layout.data_dir == data_override.resolve()
    # Other dirs still come from the production defaults (canonical form).
    assert layout.config_dir == Path("/etc/wolf-database").resolve()
    assert layout.socket_dir == Path("/var/run/wolf-database").resolve()


def test_wolf_database_production_env_var_enables_prod_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`WOLF_DATABASE_PRODUCTION=1` (without explicit kwarg) flips the layout."""
    for var in (
        "WOLF_DATABASE_DATA_DIR",
        "WOLF_DATABASE_CONFIG_DIR",
        "WOLF_DATABASE_SOCKET_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WOLF_DATABASE_PRODUCTION", "1")

    layout = resolve_layout()  # no kwarg
    assert layout.data_dir == Path("/var/lib/wolf-database/data").resolve()


def test_layout_is_frozen_dataclass() -> None:
    """DatabaseLayout is immutable — callers can't mutate a resolved layout."""
    layout = DatabaseLayout(
        data_dir=Path("/a"), config_dir=Path("/b"), socket_dir=Path("/c"),
    )
    with pytest.raises((AttributeError, TypeError)):
        layout.data_dir = Path("/x")  # type: ignore[misc]


def test_layout_postgresql_conf_path_is_inside_config_dir() -> None:
    layout = DatabaseLayout(
        data_dir=Path("/a"), config_dir=Path("/b"), socket_dir=Path("/c"),
    )
    assert layout.postgresql_conf_path == Path("/b/postgresql.conf")
    assert layout.pg_hba_conf_path == Path("/b/pg_hba.conf")


def test_layout_pid_file_is_inside_data_dir() -> None:
    """pg_ctl writes postmaster.pid into the data dir, never elsewhere."""
    layout = DatabaseLayout(
        data_dir=Path("/data"), config_dir=Path("/cfg"), socket_dir=Path("/sock"),
    )
    assert layout.pid_file_path == Path("/data/postmaster.pid")
