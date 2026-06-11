"""Self-signed CA + leaf cert primitives.

Phase 5.4-a — the pure-library layer of Wolf's HTTPS story. The
`wolf-cert` CLI (Phase 5.4-b) is the stateful shell over these
primitives. The future Wolf Knowledge Relay daemon also depends on
this library to validate wolf-server's server cert and consume its
own client cert.

Design choices, all of them deliberate
--------------------------------------
* **Default 100-year validity.** RFC 5280 forbids unlimited
  `notAfter`; 100 years is the "practical infinity" pattern that's
  safe across every TLS stack. Documented honestly so no one
  believes Wolf is somehow circumventing RFC 5280.
* **RSA-4096 keys by default.** Slightly heavier than EC but
  universally supported and the default many ops teams already
  expect for long-lived CAs.
* **`basicConstraints CA:TRUE` + `keyUsage keyCertSign,cRLSign`
  on the CA.** Required so browsers / TLS stacks treat it as a
  signing authority and refuse server-style usage.
* **`basicConstraints CA:FALSE` + `keyUsage digitalSignature,
  keyEncipherment` on leaves.** Plus `ExtendedKeyUsage` for the
  intended role (server / client / both).
* **SubjectAlternativeName carries both DNS names and IP
  addresses.** A leaf without a SAN matching the connection
  hostname won't validate in modern browsers — CN-only is dead.
* **Key files are 0600.** Cert files are 0644. Enforced via
  `os.chmod` on write; the caller's umask is irrelevant.
* **`AuthorityKeyIdentifier` + `SubjectKeyIdentifier`** on every
  cert. Required for clean chain validation in some clients.

No file ever passes through this module without a known `Path`
argument; callers control where everything lands. The `wolf-cert`
CLI in 5.4-b is the only thing that picks a default location
(`<repo>/.local/certs/`).
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Final

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

if TYPE_CHECKING:
    from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────

# 100 years — the "practical infinity" pattern. Long enough that no
# living operator will renew; safe across every TLS stack. RFC 5280
# forbids truly unlimited validity. See module docstring.
DEFAULT_VALIDITY_DAYS: Final[int] = 365 * 100

# RSA-4096 across the board. EC would be lighter but the universal-
# support story for ancient embedded clients (which a Wolf-on-Wazuh-
# host relay might one day include) is cleaner with RSA. Revisit if
# CPU cost on tiny hosts becomes a real concern.
DEFAULT_CA_KEY_SIZE: Final[int] = 4096
DEFAULT_LEAF_KEY_SIZE: Final[int] = 4096

# File permission bits applied post-write. Belt-and-braces — we
# don't rely on the caller's umask being right.
_CERT_FILE_MODE: Final[int] = 0o644
_KEY_FILE_MODE: Final[int] = 0o600


class LeafKind(Enum):
    """Which Extended Key Usage(s) to stamp on the leaf cert.

    SERVER  — `serverAuth` only. The default for wolf-server and
              wolf-dashboard leaves (browsers connecting to Wolf).
    CLIENT  — `clientAuth` only. For the future Wolf Knowledge
              Relay daemons connecting *to* wolf-server via
              mTLS — they are clients of wolf-server.
    DUAL    — both. Reserved for components that act as both
              server and client (e.g. a future Wolf-to-Wolf
              federation channel).
    """

    SERVER = "server"
    CLIENT = "client"
    DUAL = "dual"


# ──────────────────────────────────────────────────────────────────────────
# Generation
# ──────────────────────────────────────────────────────────────────────────


def generate_ca(
    *,
    common_name: str = "Wolf Root CA",
    organization: str = "Wolf",
    validity_days: int = DEFAULT_VALIDITY_DAYS,
    key_size: int = DEFAULT_CA_KEY_SIZE,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """Generate a self-signed root CA certificate + private key.

    The returned cert carries `basicConstraints CA:TRUE`, the
    keyUsage bits required for a signing CA, and Subject + Authority
    Key Identifiers. The caller is responsible for persisting both
    (`write_cert_pem` + `write_key_pem`) and protecting the private
    key with 0600 perms (`write_key_pem` does this automatically).
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
        ]
    )
    now = datetime.now(UTC)
    not_after = _bounded_not_after(now, validity_days)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(not_after)
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=0),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
    )
    cert = builder.sign(private_key=key, algorithm=hashes.SHA256())
    return key, cert


def sign_leaf(
    *,
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    common_name: str,
    san_dns: list[str],
    san_ip: list[str],
    kind: LeafKind = LeafKind.SERVER,
    validity_days: int = DEFAULT_VALIDITY_DAYS,
    key_size: int = DEFAULT_LEAF_KEY_SIZE,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """Generate a leaf cert signed by the given CA.

    `san_dns` and `san_ip` together populate the SubjectAlternativeName
    extension. A leaf must have a SAN matching the connecting
    hostname / IP — CN-only validation is dead in modern browsers.
    The function refuses to issue a leaf with an empty SAN set, since
    such a cert would be unusable.

    `kind` controls the ExtendedKeyUsage stamp. wolf-server's
    server cert is `SERVER`; a future relay client cert is `CLIENT`.
    """
    if not san_dns and not san_ip:
        raise ValueError(
            "sign_leaf: at least one DNS name or IP address is required "
            "(SAN-less certs are unusable in modern browsers)",
        )
    # The cryptography library's `public_key()` return type widens to
    # include X25519 / X448 keys which `AuthorityKeyIdentifier.from_
    # issuer_public_key()` does not accept. Every cert this library
    # mints is RSA, so the runtime check both narrows the type for
    # mypy and documents the invariant. A non-RSA CA arriving here
    # would be a coding regression — fail loudly.
    ca_pubkey = ca_cert.public_key()
    if not isinstance(ca_pubkey, rsa.RSAPublicKey):
        raise TypeError(
            f"sign_leaf: CA public key must be RSA, got {type(ca_pubkey).__name__}",
        )

    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )
    san_entries: list[x509.GeneralName] = [x509.DNSName(d) for d in san_dns]
    for ip in san_ip:
        san_entries.append(x509.IPAddress(ipaddress.ip_address(ip)))

    eku_values = _eku_for_kind(kind)

    now = datetime.now(UTC)
    not_after = _bounded_not_after(now, validity_days)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(not_after)
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage(eku_values),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(san_entries),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_pubkey),
            critical=False,
        )
    )
    cert = builder.sign(private_key=ca_key, algorithm=hashes.SHA256())
    return key, cert


def _eku_for_kind(kind: LeafKind) -> list[x509.ObjectIdentifier]:
    if kind is LeafKind.SERVER:
        return [ExtendedKeyUsageOID.SERVER_AUTH]
    if kind is LeafKind.CLIENT:
        return [ExtendedKeyUsageOID.CLIENT_AUTH]
    return [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]


def _bounded_not_after(now: datetime, validity_days: int) -> datetime:
    """Clamp `not_after` to a value the `cryptography` library and
    every TLS stack can serialise. RFC 5280 §4.1.2.5.2 allows
    `99991231235959Z` as a sentinel for "no well-defined expiration"
    but several TLS clients still trip on it. We cap at year 9999.

    The cap is applied via a pre-check on the day count (rather than
    after the `timedelta` add) because Python's `datetime.MAX` is
    year 9999, so an absurd `validity_days` value would `OverflowError`
    inside `now + timedelta(days=...)` before we got a chance to
    clamp it. Guard arithmetic, then add.
    """
    ceiling = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)
    max_safe_days = (ceiling - now).days
    if validity_days >= max_safe_days:
        return ceiling
    return now + timedelta(days=validity_days)


# ──────────────────────────────────────────────────────────────────────────
# Serialisation — PEM I/O with strict permissions
# ──────────────────────────────────────────────────────────────────────────


def write_cert_pem(cert: x509.Certificate, path: Path) -> None:
    """Write a certificate to `path` in PEM with mode 0644.

    Creates parent directories if missing. Overwrites any existing
    file at the same path (cert reissue is a normal operation).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    pem = cert.public_bytes(serialization.Encoding.PEM)
    path.write_bytes(pem)
    path.chmod(_CERT_FILE_MODE)


def write_key_pem(key: rsa.RSAPrivateKey, path: Path) -> None:
    """Write an RSA private key to `path` in PEM (unencrypted) with mode 0600.

    The 0600 permission is enforced regardless of the caller's umask.
    Creates parent directories if missing.

    Unencrypted on purpose for now: wolf-server's uvicorn launcher
    (Phase 5.4-c) reads this file directly at startup. Passphrase
    support is a future hardening item (would require either operator
    interaction or a key-management service).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(pem)
    path.chmod(_KEY_FILE_MODE)


def read_cert_pem(path: Path) -> x509.Certificate:
    """Load a single X.509 certificate from a PEM file."""
    return x509.load_pem_x509_certificate(path.read_bytes())


def read_key_pem(path: Path) -> rsa.RSAPrivateKey:
    """Load an RSA private key from a PEM file.

    Raises `TypeError` (via `isinstance` check below) if the file
    contains a non-RSA key — every key this module mints is RSA, so a
    non-RSA file is either corrupted or operator-supplied (out of
    scope for now).
    """
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError(
            f"{path}: expected an RSA private key, got {type(key).__name__}",
        )
    return key


# ──────────────────────────────────────────────────────────────────────────
# Status — human-readable cert metadata for the CLI's `status` subcommand
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CertStatus:
    """Subset of a certificate's metadata suitable for CLI display.

    All fields are derived from the cert itself; nothing depends on
    the surrounding filesystem. The CLI builds one of these per cert
    it knows about and renders them as a table.
    """

    subject_cn: str
    san_dns: tuple[str, ...]
    san_ip: tuple[str, ...]
    issuer_cn: str
    not_before: datetime
    not_after: datetime
    is_ca: bool
    fingerprint_sha256: str
    serial: int


def cert_status(cert: x509.Certificate) -> CertStatus:
    """Extract the subset of cert metadata used by `wolf-cert status`."""
    subject_cn = _name_to_cn(cert.subject)
    issuer_cn = _name_to_cn(cert.issuer)
    san_dns, san_ip = _extract_sans(cert)
    is_ca = _is_ca(cert)
    fp = cert.fingerprint(hashes.SHA256()).hex(":")
    return CertStatus(
        subject_cn=subject_cn,
        san_dns=tuple(san_dns),
        san_ip=tuple(san_ip),
        issuer_cn=issuer_cn,
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
        is_ca=is_ca,
        fingerprint_sha256=fp,
        serial=cert.serial_number,
    )


def _name_to_cn(name: x509.Name) -> str:
    attrs = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    if not attrs:
        return ""
    value = attrs[0].value
    return value if isinstance(value, str) else value.decode("utf-8", "replace")


def _extract_sans(cert: x509.Certificate) -> tuple[list[str], list[str]]:
    try:
        ext = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName,
        )
    except x509.ExtensionNotFound:
        return [], []
    san = ext.value
    dns = list(san.get_values_for_type(x509.DNSName))
    ips = [str(v) for v in san.get_values_for_type(x509.IPAddress)]
    return dns, ips


def _is_ca(cert: x509.Certificate) -> bool:
    try:
        ext = cert.extensions.get_extension_for_class(x509.BasicConstraints)
    except x509.ExtensionNotFound:
        return False
    return bool(ext.value.ca)


# ──────────────────────────────────────────────────────────────────────────
# Local SAN discovery
# ──────────────────────────────────────────────────────────────────────────


def discover_local_sans() -> tuple[list[str], list[str]]:
    """Best-effort discovery of local DNS names and IP addresses for SAN inclusion.

    Returns `(dns_names, ip_addresses)`, both deduplicated and stable-
    ordered. Always includes `localhost` and `127.0.0.1` / `::1`.
    Adds the system hostname and any IP from `socket.gethostname` /
    `socket.gethostbyname_ex` that resolves. LAN IPs from the host's
    interfaces are NOT enumerated here (cross-platform interface
    enumeration is unreliable without a third-party library) — the
    CLI in 5.4-b will offer a `wolf-cert add-host <ip-or-name>`
    subcommand for any address that isn't auto-detected.
    """
    dns: list[str] = ["localhost"]
    ips: list[str] = ["127.0.0.1", "::1"]

    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = ""
    if hostname and hostname not in dns:
        dns.append(hostname)

    # Best-effort: resolve the hostname's listed addresses. On many
    # systems this is `127.0.0.1` (already there); on others it picks
    # up the primary LAN IP — fine, harmless to include either way.
    try:
        _, _, addrs = socket.gethostbyname_ex(hostname or "localhost")
    except (OSError, socket.gaierror):
        addrs = []
    for a in addrs:
        if a not in ips:
            ips.append(a)

    return dns, ips
