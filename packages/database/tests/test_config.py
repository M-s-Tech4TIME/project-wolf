"""Tests for wolf_database.config — config-template rendering + write."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from wolf_database.config import (
    DEFAULT_PORT,
    PgHbaOptions,
    PgHbaRule,
    PostgresqlConfOptions,
    connection_url,
    write_config,
)
from wolf_database.layout import DatabaseLayout


@pytest.fixture
def layout(tmp_path: Path) -> DatabaseLayout:
    """A throw-away layout in a tmp_path so tests don't touch real dirs."""
    return DatabaseLayout(
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "cfg",
        socket_dir=tmp_path / "sock",
    )


# ─── PostgresqlConfOptions.render ──────────────────────────────────────────


def test_postgresql_conf_loads_pgvector_at_startup(layout: DatabaseLayout) -> None:
    """shared_preload_libraries must include 'vector' — pgvector
    extension can't be CREATE EXTENSIONed without it. This is the
    single hard requirement of Wolf's Postgres config."""
    body = PostgresqlConfOptions().render(layout)
    assert "shared_preload_libraries = 'vector'" in body


def test_postgresql_conf_default_listen_addresses_is_localhost(
    layout: DatabaseLayout,
) -> None:
    """Default binding is loopback — distributed deploys override."""
    body = PostgresqlConfOptions().render(layout)
    assert "listen_addresses = 'localhost'" in body


def test_postgresql_conf_unix_socket_dir_points_at_layout(
    layout: DatabaseLayout,
) -> None:
    """Socket goes into the Wolf-owned dir, not /var/run/postgresql,
    so two Postgres instances on the same host (system + wolf-database)
    don't collide."""
    body = PostgresqlConfOptions().render(layout)
    assert f"unix_socket_directories = '{layout.socket_dir}'" in body


def test_postgresql_conf_data_directory_matches_layout(
    layout: DatabaseLayout,
) -> None:
    body = PostgresqlConfOptions().render(layout)
    assert f"data_directory = '{layout.data_dir}'" in body


def test_postgresql_conf_port_override_takes_effect(
    layout: DatabaseLayout,
) -> None:
    body = PostgresqlConfOptions(port=15432).render(layout)
    assert "port = 15432" in body


def test_postgresql_conf_default_port_is_5432(layout: DatabaseLayout) -> None:
    body = PostgresqlConfOptions().render(layout)
    assert f"port = {DEFAULT_PORT}" in body
    assert DEFAULT_PORT == 5432


# ─── PgHbaOptions.render ───────────────────────────────────────────────────


def test_pg_hba_default_allows_loopback_only() -> None:
    """No LAN-host rules in the default; loopback IPv4 + IPv6 + local socket only."""
    body = PgHbaOptions().render()
    assert "local wolf wolf scram-sha-256" in body
    assert "host wolf wolf 127.0.0.1/32 scram-sha-256" in body
    assert "host wolf wolf ::1/128 scram-sha-256" in body
    # No 0.0.0.0/0 or LAN ranges by default.
    assert "0.0.0.0/0" not in body
    assert "192.168" not in body


def test_pg_hba_extra_rules_appended() -> None:
    """Distributed deploys add a hostssl rule via extra_rules."""
    extra = PgHbaRule(
        connection_type="hostssl",
        database="wolf",
        user="wolf",
        address="10.0.0.0/8",
        method="cert",
    )
    body = PgHbaOptions(extra_rules=(extra,)).render()
    assert "hostssl wolf wolf 10.0.0.0/8 cert" in body


def test_pg_hba_rule_renders_without_address_for_local() -> None:
    """`local` rules omit the address column per the pg_hba format."""
    rule = PgHbaRule(
        connection_type="local",
        database="wolf",
        user="wolf",
        address=None,
        method="trust",
    )
    assert rule.render() == "local wolf wolf trust"


# ─── write_config ──────────────────────────────────────────────────────────


def test_write_config_creates_files_with_0640(layout: DatabaseLayout) -> None:
    write_config(layout)
    pg = layout.postgresql_conf_path
    hba = layout.pg_hba_conf_path
    assert pg.exists()
    assert hba.exists()
    assert stat.S_IMODE(pg.stat().st_mode) == 0o640
    assert stat.S_IMODE(hba.stat().st_mode) == 0o640


def test_write_config_creates_config_dir_if_missing(
    tmp_path: Path,
) -> None:
    """The config dir doesn't have to pre-exist."""
    layout = DatabaseLayout(
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "new" / "nested" / "cfg",
        socket_dir=tmp_path / "sock",
    )
    assert not layout.config_dir.exists()
    write_config(layout)
    assert layout.config_dir.is_dir()
    assert layout.postgresql_conf_path.exists()


def test_write_config_is_idempotent(layout: DatabaseLayout) -> None:
    """Re-running write_config rewrites the files; no append, no diff."""
    write_config(layout)
    first = layout.postgresql_conf_path.read_text()
    write_config(layout)
    second = layout.postgresql_conf_path.read_text()
    assert first == second


# ─── connection_url ───────────────────────────────────────────────────────


def test_connection_url_via_tcp_default_form(layout: DatabaseLayout) -> None:
    """TCP mode produces the asyncpg URL wolf-server already expects."""
    url = connection_url(layout, via_socket=False)
    assert url == "postgresql+asyncpg://wolf@localhost:5432/wolf"


def test_connection_url_via_tcp_with_password(layout: DatabaseLayout) -> None:
    url = connection_url(layout, via_socket=False, password="p@ss w0rd")
    # The "@" + " " characters in the password are URL-encoded; the
    # one-letter user `wolf` is unchanged.
    assert "wolf:p%40ss%20w0rd@localhost" in url


def test_connection_url_via_socket_quotes_path(layout: DatabaseLayout) -> None:
    """Unix socket mode encodes the socket dir as a host= query param."""
    url = connection_url(layout, via_socket=True)
    assert "postgresql+asyncpg://wolf@/wolf?host=" in url
    # The encoded socket dir is in there somewhere.
    assert "sock" in url


def test_connection_url_custom_db_name(layout: DatabaseLayout) -> None:
    url = connection_url(layout, db="wolf_test", via_socket=False)
    assert url.endswith("/wolf_test")
