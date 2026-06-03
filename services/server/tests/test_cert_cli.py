"""Tests for `wolf_cert.cli` — Phase 5.4-b.

Each subcommand is exercised by calling its `cmd_*` function directly
with a parsed-like `argparse.Namespace`, against a `tmp_path`-rooted
cert directory. Avoids subprocess overhead and keeps the assertion
surface (return code, on-disk state, parsed cert contents) tight.

The CLI is the only stateful layer of the cert system; the library
underneath is already covered by `test_cert_authority.py`. These
tests therefore focus on: store layout, error / refusal paths,
flag plumbing, SAN merging, and round-tripping through actual
filesystem state.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from wolf_cert.authority import (
    LeafKind,
    cert_status,
    read_cert_pem,
)
from wolf_cert.cli import (
    CertStore,
    _ExitCode,
    _humanize_until,
    _kind_from_eku,
    cmd_add_host,
    cmd_export_ca,
    cmd_init,
    cmd_renew,
    cmd_revoke,
    cmd_status,
    main,
)

if TYPE_CHECKING:
    from pathlib import Path


# 2048 is far cheaper than the 4096 production default — these tests
# generate certs many times over per run. The library tests already
# cover the cryptographic shape; here we only care about the CLI's
# behaviour around them. We patch the library's default key size for
# every test in this module via an autouse fixture.


@pytest.fixture(autouse=True)
def _fast_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin every key generation in this module to 2048 bits — these
    tests don't care about cryptographic strength, only CLI behaviour
    around real certs. Without this each subcommand mints two 4096-bit
    keys per leaf (~15 s/test), which adds up fast."""
    monkeypatch.setattr("wolf_cert.cli.DEFAULT_VALIDITY_DAYS", 365 * 100)
    # The CLI plumbs `validity_days` through to library calls but
    # always uses the library's default key size. Override the
    # library's constants for this test run.
    monkeypatch.setattr("wolf_cert.authority.DEFAULT_CA_KEY_SIZE", 2048)
    monkeypatch.setattr("wolf_cert.authority.DEFAULT_LEAF_KEY_SIZE", 2048)


def _ns(**kwargs: object) -> argparse.Namespace:
    """Tiny convenience: build an argparse-like Namespace from kwargs."""
    return argparse.Namespace(**kwargs)


# ─── init ─────────────────────────────────────────────────────────────────


def test_init_creates_ca_and_two_leaves(tmp_path: Path) -> None:
    rc = cmd_init(_ns(
        cert_dir=str(tmp_path),
        years=100,
        ca_cn="Test Wolf Root CA",
        ca_org="Test Org",
        san_dns=[],
        san_ip=[],
    ))
    assert rc == _ExitCode.OK
    store = CertStore(root=tmp_path)
    assert store.ca_cert_path.exists()
    assert store.ca_key_path.exists()
    assert store.leaf_cert_path("server").exists()
    assert store.leaf_key_path("server").exists()
    assert store.leaf_cert_path("dashboard").exists()
    assert store.leaf_key_path("dashboard").exists()


def test_init_refuses_when_ca_exists(tmp_path: Path) -> None:
    cmd_init(_ns(
        cert_dir=str(tmp_path), years=100,
        ca_cn="CA", ca_org="Org", san_dns=[], san_ip=[],
    ))
    rc = cmd_init(_ns(
        cert_dir=str(tmp_path), years=100,
        ca_cn="CA", ca_org="Org", san_dns=[], san_ip=[],
    ))
    assert rc == _ExitCode.REFUSED


def test_init_applies_custom_san_dns_and_ip(tmp_path: Path) -> None:
    cmd_init(_ns(
        cert_dir=str(tmp_path),
        years=100,
        ca_cn="CA",
        ca_org="Org",
        san_dns=["wolf.acme.internal"],
        san_ip=["10.42.0.7"],
    ))
    store = CertStore(root=tmp_path)
    leaf = read_cert_pem(store.leaf_cert_path("server"))
    s = cert_status(leaf)
    assert "wolf.acme.internal" in s.san_dns
    assert "10.42.0.7" in s.san_ip
    # Auto-discovered SANs are still included alongside the explicit
    # ones — `localhost` should always be present.
    assert "localhost" in s.san_dns
    assert "127.0.0.1" in s.san_ip


def test_init_leaves_have_strict_key_permissions(tmp_path: Path) -> None:
    """The key files must be 0600 regardless of umask. Library-level
    test covers the wrapper; this is the integration end-to-end check
    that the CLI's writes inherit that contract."""
    old_umask = os.umask(0o000)
    try:
        cmd_init(_ns(
            cert_dir=str(tmp_path), years=100,
            ca_cn="CA", ca_org="Org", san_dns=[], san_ip=[],
        ))
    finally:
        os.umask(old_umask)
    store = CertStore(root=tmp_path)
    for path in [
        store.ca_key_path,
        store.leaf_key_path("server"),
        store.leaf_key_path("dashboard"),
    ]:
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600, f"{path} mode is {oct(mode)}, expected 0o600"


def test_init_leaves_are_server_kind(tmp_path: Path) -> None:
    """Both built-in leaves are SERVER kind — they terminate browser
    HTTPS, never act as clients. Verified by reading back the
    ExtendedKeyUsage extension."""
    cmd_init(_ns(
        cert_dir=str(tmp_path), years=100,
        ca_cn="CA", ca_org="Org", san_dns=[], san_ip=[],
    ))
    store = CertStore(root=tmp_path)
    for name in ("server", "dashboard"):
        leaf = read_cert_pem(store.leaf_cert_path(name))
        assert _kind_from_eku(leaf) == LeafKind.SERVER


# ─── status ───────────────────────────────────────────────────────────────


def test_status_errors_when_no_ca(tmp_path: Path) -> None:
    rc = cmd_status(_ns(cert_dir=str(tmp_path)))
    assert rc == _ExitCode.USER_ERROR


def test_status_prints_ca_and_leaves(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cmd_init(_ns(
        cert_dir=str(tmp_path), years=100,
        ca_cn="Test CA", ca_org="Test", san_dns=[], san_ip=[],
    ))
    capsys.readouterr()  # clear init's chatter
    rc = cmd_status(_ns(cert_dir=str(tmp_path)))
    assert rc == _ExitCode.OK
    out = capsys.readouterr().out
    assert "=== CA ===" in out
    assert "CN=Test CA" in out
    assert "leaf 'server'" in out
    assert "leaf 'dashboard'" in out
    # Each block should print SAN + fingerprint + relative-expiry.
    assert "SHA256:" in out
    assert re.search(r"in \d+ years?", out)


# ─── export-ca ────────────────────────────────────────────────────────────


def test_export_ca_pem_to_stdout(
    tmp_path: Path,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    cmd_init(_ns(
        cert_dir=str(tmp_path), years=100,
        ca_cn="CA", ca_org="Org", san_dns=[], san_ip=[],
    ))
    capsysbinary.readouterr()
    rc = cmd_export_ca(_ns(cert_dir=str(tmp_path), format="pem", out=None))
    assert rc == _ExitCode.OK
    out = capsysbinary.readouterr().out
    assert b"-----BEGIN CERTIFICATE-----" in out
    assert b"-----END CERTIFICATE-----" in out


def test_export_ca_der_to_file(tmp_path: Path) -> None:
    cmd_init(_ns(
        cert_dir=str(tmp_path), years=100,
        ca_cn="CA", ca_org="Org", san_dns=[], san_ip=[],
    ))
    target = tmp_path / "ca.der"
    rc = cmd_export_ca(_ns(cert_dir=str(tmp_path), format="der", out=str(target)))
    assert rc == _ExitCode.OK
    blob = target.read_bytes()
    # DER for an X.509 cert always starts with the SEQUENCE tag 0x30.
    assert blob[0] == 0x30


# ─── add-host ─────────────────────────────────────────────────────────────


def test_add_host_appends_dns_san_to_server(tmp_path: Path) -> None:
    cmd_init(_ns(
        cert_dir=str(tmp_path), years=100,
        ca_cn="CA", ca_org="Org", san_dns=[], san_ip=[],
    ))
    store = CertStore(root=tmp_path)
    rc = cmd_add_host(_ns(
        cert_dir=str(tmp_path),
        host="wolf.example.org",
        leaf="server",
    ))
    assert rc == _ExitCode.OK
    srv = cert_status(read_cert_pem(store.leaf_cert_path("server")))
    dash = cert_status(read_cert_pem(store.leaf_cert_path("dashboard")))
    assert "wolf.example.org" in srv.san_dns
    # `--leaf=server` means the dashboard leaf was NOT touched.
    assert "wolf.example.org" not in dash.san_dns


def test_add_host_classifies_ip_correctly(tmp_path: Path) -> None:
    cmd_init(_ns(
        cert_dir=str(tmp_path), years=100,
        ca_cn="CA", ca_org="Org", san_dns=[], san_ip=[],
    ))
    rc = cmd_add_host(_ns(
        cert_dir=str(tmp_path), host="192.168.42.7", leaf="all",
    ))
    assert rc == _ExitCode.OK
    store = CertStore(root=tmp_path)
    for name in ("server", "dashboard"):
        s = cert_status(read_cert_pem(store.leaf_cert_path(name)))
        assert "192.168.42.7" in s.san_ip


def test_add_host_idempotent_on_duplicate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Re-adding a SAN that's already present should print a 'skipping'
    line and leave the cert untouched."""
    cmd_init(_ns(
        cert_dir=str(tmp_path), years=100,
        ca_cn="CA", ca_org="Org",
        san_dns=["wolf.local"], san_ip=[],
    ))
    capsys.readouterr()
    rc = cmd_add_host(_ns(
        cert_dir=str(tmp_path), host="wolf.local", leaf="all",
    ))
    assert rc == _ExitCode.OK
    out = capsys.readouterr().out
    assert "already present" in out


def test_add_host_errors_when_no_ca(tmp_path: Path) -> None:
    rc = cmd_add_host(_ns(
        cert_dir=str(tmp_path), host="x.example", leaf="all",
    ))
    assert rc == _ExitCode.USER_ERROR


# ─── renew ────────────────────────────────────────────────────────────────


def test_renew_reissues_leaves_and_extends_not_after(tmp_path: Path) -> None:
    cmd_init(_ns(
        cert_dir=str(tmp_path), years=1,
        ca_cn="CA", ca_org="Org", san_dns=[], san_ip=[],
    ))
    store = CertStore(root=tmp_path)
    before = cert_status(read_cert_pem(store.leaf_cert_path("server")))
    before_serial = before.serial

    rc = cmd_renew(_ns(
        cert_dir=str(tmp_path), years=100, leaf="all", ca=False,
    ))
    assert rc == _ExitCode.OK
    after = cert_status(read_cert_pem(store.leaf_cert_path("server")))
    assert after.not_after > before.not_after
    # Serial must differ — a renew is a NEW certificate, not an update
    # of the existing one.
    assert after.serial != before_serial


def test_renew_preserves_sans(tmp_path: Path) -> None:
    cmd_init(_ns(
        cert_dir=str(tmp_path), years=100,
        ca_cn="CA", ca_org="Org",
        san_dns=["wolf.acme.internal"],
        san_ip=["10.42.0.7"],
    ))
    store = CertStore(root=tmp_path)
    before = cert_status(read_cert_pem(store.leaf_cert_path("server")))

    cmd_renew(_ns(
        cert_dir=str(tmp_path), years=100, leaf="all", ca=False,
    ))
    after = cert_status(read_cert_pem(store.leaf_cert_path("server")))
    assert set(after.san_dns) == set(before.san_dns)
    assert set(after.san_ip) == set(before.san_ip)


def test_renew_can_reissue_the_ca(tmp_path: Path) -> None:
    cmd_init(_ns(
        cert_dir=str(tmp_path), years=1,
        ca_cn="CA", ca_org="Org", san_dns=[], san_ip=[],
    ))
    store = CertStore(root=tmp_path)
    before_ca = cert_status(read_cert_pem(store.ca_cert_path))

    cmd_renew(_ns(
        cert_dir=str(tmp_path), years=100, leaf="all", ca=True,
    ))
    after_ca = cert_status(read_cert_pem(store.ca_cert_path))
    assert after_ca.not_after > before_ca.not_after
    assert after_ca.serial != before_ca.serial
    # Leaves should be reissued under the new CA (issuer's serial /
    # fingerprint will have flipped). Easiest check: leaf's issuer
    # CN is still "CA" (we re-used the existing CN).
    leaf = cert_status(read_cert_pem(store.leaf_cert_path("server")))
    assert leaf.issuer_cn == "CA"


# ─── revoke ───────────────────────────────────────────────────────────────


def test_revoke_refuses_without_yes(tmp_path: Path) -> None:
    cmd_init(_ns(
        cert_dir=str(tmp_path), years=100,
        ca_cn="CA", ca_org="Org", san_dns=[], san_ip=[],
    ))
    rc = cmd_revoke(_ns(cert_dir=str(tmp_path), yes=False))
    assert rc == _ExitCode.REFUSED
    assert CertStore(root=tmp_path).ca_exists()  # nothing removed


def test_revoke_with_yes_removes_cert_dir(tmp_path: Path) -> None:
    cmd_init(_ns(
        cert_dir=str(tmp_path), years=100,
        ca_cn="CA", ca_org="Org", san_dns=[], san_ip=[],
    ))
    rc = cmd_revoke(_ns(cert_dir=str(tmp_path), yes=True))
    assert rc == _ExitCode.OK
    assert not tmp_path.exists()


# ─── main() integration ──────────────────────────────────────────────────


def test_main_returns_zero_on_init_and_status(tmp_path: Path) -> None:
    rc1 = main(["init", "--cert-dir", str(tmp_path), "--years", "100"])
    assert rc1 == 0
    rc2 = main(["status", "--cert-dir", str(tmp_path)])
    assert rc2 == 0


def test_main_subprocess_round_trip(tmp_path: Path) -> None:
    """Real end-to-end: spawn `python -m wolf_cert init` in a
    subprocess and verify exit code + on-disk state. Catches anything
    the in-process tests can't — argparse plumbing, console-script
    wiring, exit-code propagation. Subprocess-spawn cost (one model
    instantiation is none — we just generate keys) is fine."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, "-m", "wolf_cert", "init",
         "--cert-dir", str(tmp_path), "--years", "100"],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    store = CertStore(root=tmp_path)
    assert store.ca_exists()
    assert store.leaf_cert_path("server").exists()


# ─── tiny utilities ──────────────────────────────────────────────────────


def test_humanize_until_handles_years_months_and_expired() -> None:
    from datetime import timedelta

    now = __import__("datetime").datetime.now(__import__("datetime").UTC)
    assert "EXPIRED" in _humanize_until(now - timedelta(days=1))
    assert "year" in _humanize_until(now + timedelta(days=365 * 5))
    assert "month" in _humanize_until(now + timedelta(days=90))
    assert "day" in _humanize_until(now + timedelta(days=3))
