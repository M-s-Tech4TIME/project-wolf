"""Tests for `wolf_cert.authority` — Phase 5.4-a (the pure library).

Verifies the cryptographic primitives in isolation: CA generation
produces a valid signing cert, leaves chain to the CA, SANs propagate
correctly, file permissions are strict, and the status parser returns
what the CLI will display in 5.4-b. No filesystem state outside of
the per-test `tmp_path`; no network; no external state.
"""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID
from wolf_cert.authority import (
    DEFAULT_VALIDITY_DAYS,
    CertStatus,
    LeafKind,
    cert_status,
    discover_local_sans,
    generate_ca,
    read_cert_pem,
    read_key_pem,
    sign_leaf,
    write_cert_pem,
    write_key_pem,
)

if TYPE_CHECKING:
    from pathlib import Path


# Small key size for tests — 4096-bit RSA generation is too slow for
# a per-test cost (~3s each). 2048 is still real RSA, just faster.
TEST_KEY_SIZE = 2048


# ─── CA generation ────────────────────────────────────────────────────────


def test_generate_ca_returns_key_and_self_signed_cert():
    key, cert = generate_ca(
        common_name="Test Wolf Root CA",
        organization="Test Org",
        validity_days=30,
        key_size=TEST_KEY_SIZE,
    )
    assert isinstance(key, rsa.RSAPrivateKey)
    assert isinstance(cert, x509.Certificate)
    assert cert.subject == cert.issuer  # self-signed
    assert cert.subject.rfc4514_string().count("CN=Test Wolf Root CA") == 1


def test_generate_ca_marks_basic_constraints_ca_true():
    _, cert = generate_ca(validity_days=30, key_size=TEST_KEY_SIZE)
    bc = cert.extensions.get_extension_for_class(x509.BasicConstraints)
    assert bc.value.ca is True
    assert bc.critical is True


def test_generate_ca_key_usage_has_cert_sign_and_crl_sign():
    _, cert = generate_ca(validity_days=30, key_size=TEST_KEY_SIZE)
    ku = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    assert ku.key_cert_sign is True
    assert ku.crl_sign is True
    # Defence in depth: a CA cert should NOT carry server-leaf usage bits.
    assert ku.digital_signature is False
    assert ku.key_encipherment is False


def test_generate_ca_has_subject_key_identifier():
    _, cert = generate_ca(validity_days=30, key_size=TEST_KEY_SIZE)
    # Required for clean chain validation — should not raise.
    cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)


def test_generate_ca_default_validity_is_100_years():
    """Sanity-check the 'practical infinity' default. Validity is in
    days, not years, so the actual `not_after` is approximately
    today + 36500d. We check it's within a couple of days of that
    target — calendar drift / leap years can shift it slightly."""
    _, cert = generate_ca(key_size=TEST_KEY_SIZE)  # use default validity
    delta = cert.not_valid_after_utc - cert.not_valid_before_utc
    assert delta.days == DEFAULT_VALIDITY_DAYS == 365 * 100


# ─── Leaf generation ──────────────────────────────────────────────────────


def _make_test_ca():
    """Convenience fixture: returns (ca_key, ca_cert) at test key size."""
    return generate_ca(
        common_name="Test Wolf Root CA",
        validity_days=30,
        key_size=TEST_KEY_SIZE,
    )


def test_sign_leaf_chains_to_ca():
    ca_key, ca_cert = _make_test_ca()
    _, leaf = sign_leaf(
        ca_key=ca_key,
        ca_cert=ca_cert,
        common_name="test.example",
        san_dns=["test.example"],
        san_ip=[],
        validity_days=30,
        key_size=TEST_KEY_SIZE,
    )
    # Leaf's issuer matches CA's subject.
    assert leaf.issuer == ca_cert.subject
    # And the CA's public key actually signed the leaf — verify by
    # rebuilding the verification via cryptography's signature check.
    from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15

    ca_cert.public_key().verify(
        leaf.signature,
        leaf.tbs_certificate_bytes,
        PKCS1v15(),
        leaf.signature_hash_algorithm,  # type: ignore[arg-type]
    )


def test_sign_leaf_is_not_a_ca():
    ca_key, ca_cert = _make_test_ca()
    _, leaf = sign_leaf(
        ca_key=ca_key,
        ca_cert=ca_cert,
        common_name="test.example",
        san_dns=["test.example"],
        san_ip=[],
        validity_days=30,
        key_size=TEST_KEY_SIZE,
    )
    bc = leaf.extensions.get_extension_for_class(x509.BasicConstraints)
    assert bc.value.ca is False


def test_sign_leaf_includes_dns_and_ip_sans():
    ca_key, ca_cert = _make_test_ca()
    _, leaf = sign_leaf(
        ca_key=ca_key,
        ca_cert=ca_cert,
        common_name="wolf.local",
        san_dns=["wolf.local", "wolf"],
        san_ip=["192.168.1.10", "127.0.0.1"],
        validity_days=30,
        key_size=TEST_KEY_SIZE,
    )
    san = leaf.extensions.get_extension_for_class(
        x509.SubjectAlternativeName,
    ).value
    dns_names = san.get_values_for_type(x509.DNSName)
    ip_addrs = [str(v) for v in san.get_values_for_type(x509.IPAddress)]
    assert "wolf.local" in dns_names
    assert "wolf" in dns_names
    assert "192.168.1.10" in ip_addrs
    assert "127.0.0.1" in ip_addrs


def test_sign_leaf_refuses_empty_sans():
    ca_key, ca_cert = _make_test_ca()
    with pytest.raises(ValueError, match="at least one DNS name or IP"):
        sign_leaf(
            ca_key=ca_key,
            ca_cert=ca_cert,
            common_name="empty.example",
            san_dns=[],
            san_ip=[],
            validity_days=30,
            key_size=TEST_KEY_SIZE,
        )


def test_sign_leaf_server_kind_has_server_auth_eku():
    ca_key, ca_cert = _make_test_ca()
    _, leaf = sign_leaf(
        ca_key=ca_key,
        ca_cert=ca_cert,
        common_name="srv.example",
        san_dns=["srv.example"],
        san_ip=[],
        kind=LeafKind.SERVER,
        validity_days=30,
        key_size=TEST_KEY_SIZE,
    )
    eku = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in eku
    assert ExtendedKeyUsageOID.CLIENT_AUTH not in eku


def test_sign_leaf_client_kind_has_client_auth_eku():
    """Future Wolf Knowledge Relay leaves use this kind — mTLS as a client
    of wolf-server."""
    ca_key, ca_cert = _make_test_ca()
    _, leaf = sign_leaf(
        ca_key=ca_key,
        ca_cert=ca_cert,
        common_name="relay.acme.local",
        san_dns=["relay.acme.local"],
        san_ip=[],
        kind=LeafKind.CLIENT,
        validity_days=30,
        key_size=TEST_KEY_SIZE,
    )
    eku = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.CLIENT_AUTH in eku
    assert ExtendedKeyUsageOID.SERVER_AUTH not in eku


def test_sign_leaf_dual_kind_has_both_eku():
    ca_key, ca_cert = _make_test_ca()
    _, leaf = sign_leaf(
        ca_key=ca_key,
        ca_cert=ca_cert,
        common_name="dual.example",
        san_dns=["dual.example"],
        san_ip=[],
        kind=LeafKind.DUAL,
        validity_days=30,
        key_size=TEST_KEY_SIZE,
    )
    eku = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in eku
    assert ExtendedKeyUsageOID.CLIENT_AUTH in eku


def test_sign_leaf_validity_clamps_to_year_9999():
    """An absurd request (10 000 years) should clamp at the year 9999
    ceiling. RFC 5280 §4.1.2.5.2 allows the `99991231235959Z`
    sentinel for "no well-defined expiration," but several TLS
    clients trip on values past that boundary, so the library
    clamps defensively. We pick a `validity_days` that's certain to
    overshoot regardless of when in the millennium the test runs."""
    ca_key, ca_cert = _make_test_ca()
    _, leaf = sign_leaf(
        ca_key=ca_key,
        ca_cert=ca_cert,
        common_name="long.example",
        san_dns=["long.example"],
        san_ip=[],
        validity_days=365 * 10_000,
        key_size=TEST_KEY_SIZE,
    )
    assert leaf.not_valid_after_utc.year == 9999


# ─── PEM I/O + file permissions ───────────────────────────────────────────


def test_write_cert_pem_roundtrips(tmp_path: Path):
    _, cert = generate_ca(validity_days=30, key_size=TEST_KEY_SIZE)
    target = tmp_path / "ca-cert.pem"
    write_cert_pem(cert, target)
    loaded = read_cert_pem(target)
    assert loaded.serial_number == cert.serial_number
    assert loaded.subject == cert.subject


def test_write_key_pem_roundtrips(tmp_path: Path):
    key, _ = generate_ca(validity_days=30, key_size=TEST_KEY_SIZE)
    target = tmp_path / "ca-key.pem"
    write_key_pem(key, target)
    loaded = read_key_pem(target)
    # Compare via public-key derivation rather than direct key
    # equality (RSAPrivateKey isn't __eq__'d).
    assert loaded.public_key().public_numbers() == key.public_key().public_numbers()


def test_write_key_pem_is_mode_0600(tmp_path: Path):
    """The whole point of the wrapper — caller's umask is irrelevant.
    Without the explicit chmod the file would inherit umask-derived
    permissions (typically 0644 on Linux) and leak the private key
    to any local user. Enforce 0600."""
    key, _ = generate_ca(validity_days=30, key_size=TEST_KEY_SIZE)
    target = tmp_path / "ca-key.pem"

    # Permissive umask: confirm the wrapper still narrows to 0600.
    old_umask = os.umask(0o000)
    try:
        write_key_pem(key, target)
    finally:
        os.umask(old_umask)

    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600, f"key file mode is {oct(mode)}, expected 0o600"


def test_write_cert_pem_is_mode_0644(tmp_path: Path):
    _, cert = generate_ca(validity_days=30, key_size=TEST_KEY_SIZE)
    target = tmp_path / "ca-cert.pem"

    old_umask = os.umask(0o077)  # restrictive — would default file to 0600
    try:
        write_cert_pem(cert, target)
    finally:
        os.umask(old_umask)

    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o644, f"cert file mode is {oct(mode)}, expected 0o644"


def test_write_cert_pem_creates_parent_directories(tmp_path: Path):
    _, cert = generate_ca(validity_days=30, key_size=TEST_KEY_SIZE)
    target = tmp_path / "deeply" / "nested" / "path" / "cert.pem"
    write_cert_pem(cert, target)
    assert target.exists()


def test_read_key_pem_rejects_non_rsa(tmp_path: Path):
    """The library only mints RSA keys — a non-RSA file in our store
    is either corruption or a stray operator-supplied file. Fail
    fast rather than carrying the wrong key type through wolf-server's
    startup."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    target = tmp_path / "ec-key.pem"
    ec_key = ec.generate_private_key(ec.SECP256R1())
    pem = ec_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    target.write_bytes(pem)

    with pytest.raises(TypeError, match="expected an RSA private key"):
        read_key_pem(target)


# ─── Status parser ────────────────────────────────────────────────────────


def test_cert_status_extracts_ca_fields():
    _, ca_cert = generate_ca(
        common_name="Test Wolf Root CA",
        validity_days=30,
        key_size=TEST_KEY_SIZE,
    )
    status = cert_status(ca_cert)
    assert isinstance(status, CertStatus)
    assert status.subject_cn == "Test Wolf Root CA"
    assert status.issuer_cn == "Test Wolf Root CA"  # self-signed
    assert status.is_ca is True
    assert status.san_dns == ()
    assert status.san_ip == ()
    assert status.serial == ca_cert.serial_number
    # Fingerprint format is colon-separated hex.
    assert ":" in status.fingerprint_sha256
    assert len(status.fingerprint_sha256.split(":")) == 32  # 32 bytes


def test_cert_status_extracts_leaf_sans():
    ca_key, ca_cert = _make_test_ca()
    _, leaf = sign_leaf(
        ca_key=ca_key,
        ca_cert=ca_cert,
        common_name="wolf.local",
        san_dns=["wolf.local", "wolf"],
        san_ip=["10.0.0.5"],
        validity_days=30,
        key_size=TEST_KEY_SIZE,
    )
    status = cert_status(leaf)
    assert status.subject_cn == "wolf.local"
    assert status.is_ca is False
    assert set(status.san_dns) == {"wolf.local", "wolf"}
    assert set(status.san_ip) == {"10.0.0.5"}


def test_cert_status_validity_is_timezone_aware():
    """The CLI in 5.4-b will render expiry as 'X years from now' — for
    that arithmetic to work, the parsed datetimes must be UTC-aware,
    not naive. cryptography 42+ exposes `not_valid_*_utc` properties
    that return timezone-aware datetimes; we use those."""
    _, cert = generate_ca(validity_days=30, key_size=TEST_KEY_SIZE)
    status = cert_status(cert)
    assert status.not_before.tzinfo is not None
    assert status.not_after.tzinfo is not None
    # Sanity: not_after should be ~30 days in the future from now.
    delta = status.not_after - datetime.now(UTC)
    assert 29 <= delta.days <= 30


# ─── Local SAN discovery ──────────────────────────────────────────────────


def test_discover_local_sans_includes_localhost_and_loopback():
    """Whatever else gets picked up, the loopback names + addresses
    MUST be present so a `localhost` browser connection always
    validates after init."""
    dns, ips = discover_local_sans()
    assert "localhost" in dns
    assert "127.0.0.1" in ips
    assert "::1" in ips


def test_discover_local_sans_deduplicates():
    """If the system hostname resolves to 127.0.0.1 (the common
    /etc/hosts shape), the loopback IP shouldn't appear twice in the
    returned list."""
    dns, ips = discover_local_sans()
    assert len(dns) == len(set(dns))
    assert len(ips) == len(set(ips))
