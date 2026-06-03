"""Tests for the wolf-server launcher's TLS resolution — Phase 5.4-c (renamed Phase 5.5).

Pure-function coverage of `resolve_tls(cert_path, key_path)`. The
launcher's actual uvicorn call is glue that we don't exercise here
(would require subprocess + port binding); the decision logic is
where the bugs would live.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wolf_server.__main__ import resolve_tls

if TYPE_CHECKING:
    from pathlib import Path


def test_resolve_tls_picks_https_when_both_files_present(tmp_path: Path) -> None:
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_bytes(b"-----BEGIN CERTIFICATE-----\n...\n")
    key.write_bytes(b"-----BEGIN RSA PRIVATE KEY-----\n...\n")

    tls = resolve_tls(str(cert), str(key))
    assert tls.use_https is True
    assert tls.cert_path == cert
    assert tls.key_path == key
    assert "present" in tls.reason


def test_resolve_tls_falls_back_to_http_when_neither_file_present(
    tmp_path: Path,
) -> None:
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    # Neither file written.

    tls = resolve_tls(str(cert), str(key))
    assert tls.use_https is False
    assert tls.cert_path is None
    assert tls.key_path is None
    assert "No TLS cert" in tls.reason
    assert "wolf-cert init" in tls.reason


def test_resolve_tls_falls_back_to_http_when_key_missing(tmp_path: Path) -> None:
    """The cert exists but the key doesn't — half-loaded TLS would
    produce confusing handshake errors at request time. Refuse to
    start HTTPS in this state; surface the broken pair in the reason
    string so the operator can fix it."""
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_bytes(b"cert")

    tls = resolve_tls(str(cert), str(key))
    assert tls.use_https is False
    assert "incomplete" in tls.reason
    assert str(key) in tls.reason


def test_resolve_tls_falls_back_to_http_when_cert_missing(tmp_path: Path) -> None:
    """Mirror of the key-missing case — equally broken, equally
    surfaced. Symmetric handling matters because operators often see
    these via `rm -rf .local/certs/<role>/cert.pem` accidents."""
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    key.write_bytes(b"key")

    tls = resolve_tls(str(cert), str(key))
    assert tls.use_https is False
    assert "incomplete" in tls.reason
    assert str(cert) in tls.reason


def test_resolve_tls_directory_at_cert_path_is_not_a_file(tmp_path: Path) -> None:
    """If `tls_cert_path` accidentally points at a directory (e.g.
    operator forgot the `/cert.pem` suffix), `is_file()` returns
    False and we fall back to HTTP rather than blowing up at uvicorn
    startup."""
    cert_dir = tmp_path / "cert.pem"
    cert_dir.mkdir()
    key = tmp_path / "key.pem"

    tls = resolve_tls(str(cert_dir), str(key))
    assert tls.use_https is False


def test_tls_resolution_use_https_property_is_consistent(tmp_path: Path) -> None:
    """`use_https` is derived from both paths being non-None; assert
    no path-skew can land us in a state where it's True but one of
    the paths is None."""
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_bytes(b"x")
    key.write_bytes(b"x")

    tls = resolve_tls(str(cert), str(key))
    if tls.use_https:
        assert tls.cert_path is not None
        assert tls.key_path is not None
