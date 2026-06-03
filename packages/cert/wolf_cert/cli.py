"""`wolf-cert` CLI — Phase 5.4-b.

Thin shell over `wolf_cert.authority`. Owns the on-disk layout
(default `.local/certs/<role>/{cert,key}.pem`), translates CLI flags
to library calls, and prints human-readable status. Argparse stdlib
only — no new deps.

Layout
------
By default the CLI manages this tree under `--cert-dir` (default
`.local/certs/`). Layout follows ADR 0016 (component-named leaves):

```
<cert-dir>/
  ca/
    ca-cert.pem      0644
    ca-key.pem       0600
  server/
    cert.pem         0644
    key.pem          0600
  dashboard/
    cert.pem         0644
    key.pem          0600
```

Future-relay extension (Phase: Wolf Knowledge Relay): per-tenant
relay leaves under `<cert-dir>/relay-<tenant>/`. The `_BUILTIN_LEAVES`
tuple below is the single place that decides which leaves get
minted by `wolf-cert init`; adding a relay subcommand later is
purely additive.

Subcommands
-----------
* `init`        — generate CA + server + dashboard leaves.
                  Refuses if the CA already exists (use `renew`
                  or `revoke + init`).
* `status`      — show subject / SANs / validity / fingerprint
                  for the CA and every leaf the store knows about.
* `export-ca`   — print the CA cert to stdout (or `--out <path>`)
                  in PEM or DER. The one-time per-machine trust
                  install step.
* `add-host`    — add a SAN (DNS or IP) to existing leaves and
                  reissue them. CA key/cert untouched.
* `renew`       — reissue every leaf (and optionally the CA) with
                  fresh validity, keeping all existing SANs.
* `revoke`      — delete every cert + key in `<cert-dir>`. Force
                  a re-init.

Exit codes
----------
0 success · 2 user error (missing files, bad flags) · 3 refusal
(would clobber existing state without `--force`-style override).

All commands take `--cert-dir` so an operator running multiple
environments off the same workstation can keep them separate.
"""

from __future__ import annotations

import argparse
import ipaddress
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import serialization

from .authority import (
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
    from collections.abc import Sequence

    from cryptography import x509


# ──────────────────────────────────────────────────────────────────────────
# Store layout
# ──────────────────────────────────────────────────────────────────────────

DEFAULT_CERT_DIR = Path(".local/certs")


@dataclass(frozen=True)
class CertStore:
    """Filesystem layout for Wolf's cert directory. Pure paths — no I/O
    side effects from construction. Every command takes a `CertStore`
    and reads/writes via the library."""

    root: Path

    @property
    def ca_dir(self) -> Path:
        return self.root / "ca"

    @property
    def ca_cert_path(self) -> Path:
        return self.ca_dir / "ca-cert.pem"

    @property
    def ca_key_path(self) -> Path:
        return self.ca_dir / "ca-key.pem"

    def leaf_dir(self, name: str) -> Path:
        return self.root / name

    def leaf_cert_path(self, name: str) -> Path:
        return self.leaf_dir(name) / "cert.pem"

    def leaf_key_path(self, name: str) -> Path:
        return self.leaf_dir(name) / "key.pem"

    def ca_exists(self) -> bool:
        return self.ca_cert_path.exists() and self.ca_key_path.exists()

    def leaves_present(self) -> list[str]:
        """Names of every leaf directory under `root` that has BOTH
        cert.pem and key.pem present. Skips `ca/` and anything else
        that doesn't look like a leaf."""
        if not self.root.exists():
            return []
        out: list[str] = []
        for child in sorted(self.root.iterdir()):
            if not child.is_dir() or child.name == "ca":
                continue
            if (child / "cert.pem").exists() and (child / "key.pem").exists():
                out.append(child.name)
        return out


# ──────────────────────────────────────────────────────────────────────────
# Built-in leaf catalog
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _LeafSpec:
    name: str
    common_name: str
    kind: LeafKind


# The leaves `wolf-cert init` mints by default. Adding a new entry
# here teaches every other subcommand about it for free — `status`
# discovers it via the store, `add-host` / `renew` operate on it,
# `revoke` cleans it up.
# Phase 5.5 component rename: orchestrator → server, frontend → dashboard.
# Per ADR 0016, these are the always-minted-on-init leaves for an
# all-in-one Wolf install. Distributed deployments still benefit from
# the same set on the operator's admin workstation; the operator
# distributes each leaf to the host that runs the matching component.
# A future `wolf-cert issue-relay <tenant>` subcommand will append
# tenant-scoped CLIENT-kind leaves alongside these.
_BUILTIN_LEAVES: tuple[_LeafSpec, ...] = (
    _LeafSpec(name="server", common_name="wolf-server", kind=LeafKind.SERVER),
    _LeafSpec(name="dashboard", common_name="wolf-dashboard", kind=LeafKind.SERVER),
)


class _ExitCode(int, Enum):
    OK = 0
    USER_ERROR = 2
    REFUSED = 3


# ──────────────────────────────────────────────────────────────────────────
# Subcommands — each takes a parsed `argparse.Namespace` and returns
# an exit code. Pure functions over the CertStore; testable directly.
# ──────────────────────────────────────────────────────────────────────────


def cmd_init(args: argparse.Namespace) -> int:
    store = CertStore(root=Path(args.cert_dir).resolve())
    if store.ca_exists():
        _eprint(
            f"refusing to init: CA already exists at {store.ca_cert_path}\n"
            "  use `wolf-cert renew` to extend validity, or "
            "`wolf-cert revoke` then `wolf-cert init` for a fresh CA",
        )
        return _ExitCode.REFUSED

    # Mint the CA.
    print(f"→ generating Wolf Root CA ({args.years}-year validity)")
    ca_key, ca_cert = generate_ca(
        common_name=args.ca_cn,
        organization=args.ca_org,
        validity_days=args.years * 365,
    )
    write_cert_pem(ca_cert, store.ca_cert_path)
    write_key_pem(ca_key, store.ca_key_path)
    print(f"  CA cert: {store.ca_cert_path}")
    print(f"  CA key:  {store.ca_key_path}")

    # Determine SANs once and apply to every leaf — wolf-server and
    # wolf-dashboard should match the same hostname / IP set so the
    # browser doesn't reject one and accept the other.
    san_dns, san_ip = _resolve_sans(args)
    if not san_dns and not san_ip:
        _eprint(
            "refusing to init: no SANs to use. Both local hostname/IP "
            "discovery and CLI flags came up empty.",
        )
        return _ExitCode.USER_ERROR
    print(f"  SAN DNS: {', '.join(san_dns) if san_dns else '(none)'}")
    print(f"  SAN IP:  {', '.join(san_ip) if san_ip else '(none)'}")

    # Mint each built-in leaf.
    for spec in _BUILTIN_LEAVES:
        print(f"→ signing leaf '{spec.name}' (CN={spec.common_name})")
        leaf_key, leaf_cert = sign_leaf(
            ca_key=ca_key,
            ca_cert=ca_cert,
            common_name=spec.common_name,
            san_dns=san_dns,
            san_ip=san_ip,
            kind=spec.kind,
            validity_days=args.years * 365,
        )
        write_cert_pem(leaf_cert, store.leaf_cert_path(spec.name))
        write_key_pem(leaf_key, store.leaf_key_path(spec.name))
        print(f"  cert: {store.leaf_cert_path(spec.name)}")
        print(f"  key:  {store.leaf_key_path(spec.name)}")

    print("")
    print("✓ done. To install the CA in your OS / browser trust stores:")
    print(f"    wolf-cert export-ca --cert-dir {args.cert_dir}")
    return _ExitCode.OK


def cmd_status(args: argparse.Namespace) -> int:
    store = CertStore(root=Path(args.cert_dir).resolve())
    if not store.ca_exists():
        _eprint(
            f"no CA at {store.ca_cert_path} — run `wolf-cert init` first",
        )
        return _ExitCode.USER_ERROR

    ca_status = cert_status(read_cert_pem(store.ca_cert_path))
    _print_status_block("CA", ca_status)

    leaves = store.leaves_present()
    if not leaves:
        print("(no leaves issued yet)")
        return _ExitCode.OK
    for name in leaves:
        leaf = read_cert_pem(store.leaf_cert_path(name))
        _print_status_block(f"leaf '{name}'", cert_status(leaf))
    return _ExitCode.OK


def cmd_export_ca(args: argparse.Namespace) -> int:
    store = CertStore(root=Path(args.cert_dir).resolve())
    if not store.ca_exists():
        _eprint(f"no CA at {store.ca_cert_path} — run `wolf-cert init` first")
        return _ExitCode.USER_ERROR

    cert = read_cert_pem(store.ca_cert_path)
    if args.format == "der":
        payload = cert.public_bytes(serialization.Encoding.DER)
    else:
        payload = cert.public_bytes(serialization.Encoding.PEM)

    if args.out is None or args.out == "-":
        sys.stdout.buffer.write(payload)
        if args.format == "pem" and not payload.endswith(b"\n"):
            sys.stdout.buffer.write(b"\n")
    else:
        Path(args.out).write_bytes(payload)
        print(f"→ wrote {args.out} ({args.format.upper()}, "
              f"{len(payload)} bytes)")
    return _ExitCode.OK


def cmd_add_host(args: argparse.Namespace) -> int:
    store = CertStore(root=Path(args.cert_dir).resolve())
    if not store.ca_exists():
        _eprint(f"no CA at {store.ca_cert_path} — run `wolf-cert init` first")
        return _ExitCode.USER_ERROR

    leaves = _select_leaves(store, args.leaf)
    if not leaves:
        _eprint(f"no matching leaves to update (--leaf={args.leaf})")
        return _ExitCode.USER_ERROR

    new_dns, new_ip = _classify_host(args.host)
    ca_cert = read_cert_pem(store.ca_cert_path)
    ca_key = read_key_pem(store.ca_key_path)

    for name in leaves:
        existing = read_cert_pem(store.leaf_cert_path(name))
        san_dns, san_ip = _existing_sans(existing)
        if new_dns and new_dns[0] in san_dns:
            print(f"→ leaf '{name}': '{args.host}' already present, skipping")
            continue
        if new_ip and new_ip[0] in san_ip:
            print(f"→ leaf '{name}': '{args.host}' already present, skipping")
            continue
        merged_dns = san_dns + new_dns
        merged_ip = san_ip + new_ip
        cn = _name_to_cn(existing)
        kind = _kind_from_eku(existing)
        print(f"→ reissuing leaf '{name}' with added SAN '{args.host}'")
        leaf_key, leaf_cert = sign_leaf(
            ca_key=ca_key,
            ca_cert=ca_cert,
            common_name=cn,
            san_dns=merged_dns,
            san_ip=merged_ip,
            kind=kind,
            validity_days=DEFAULT_VALIDITY_DAYS,
        )
        write_cert_pem(leaf_cert, store.leaf_cert_path(name))
        write_key_pem(leaf_key, store.leaf_key_path(name))
    return _ExitCode.OK


def cmd_renew(args: argparse.Namespace) -> int:
    store = CertStore(root=Path(args.cert_dir).resolve())
    if not store.ca_exists():
        _eprint(f"no CA at {store.ca_cert_path} — run `wolf-cert init` first")
        return _ExitCode.USER_ERROR

    validity_days = args.years * 365

    if args.ca:
        print(f"→ renewing CA ({args.years}-year validity)")
        existing = read_cert_pem(store.ca_cert_path)
        ca_cn = _name_to_cn(existing)
        ca_key, ca_cert = generate_ca(
            common_name=ca_cn,
            validity_days=validity_days,
        )
        write_cert_pem(ca_cert, store.ca_cert_path)
        write_key_pem(ca_key, store.ca_key_path)
    else:
        ca_cert = read_cert_pem(store.ca_cert_path)
        ca_key = read_key_pem(store.ca_key_path)

    leaves = _select_leaves(store, args.leaf)
    for name in leaves:
        existing = read_cert_pem(store.leaf_cert_path(name))
        san_dns, san_ip = _existing_sans(existing)
        cn = _name_to_cn(existing)
        kind = _kind_from_eku(existing)
        print(f"→ reissuing leaf '{name}' ({args.years}-year validity)")
        leaf_key, leaf_cert = sign_leaf(
            ca_key=ca_key,
            ca_cert=ca_cert,
            common_name=cn,
            san_dns=san_dns,
            san_ip=san_ip,
            kind=kind,
            validity_days=validity_days,
        )
        write_cert_pem(leaf_cert, store.leaf_cert_path(name))
        write_key_pem(leaf_key, store.leaf_key_path(name))
    return _ExitCode.OK


def cmd_revoke(args: argparse.Namespace) -> int:
    store = CertStore(root=Path(args.cert_dir).resolve())
    if not store.root.exists():
        print(f"nothing to revoke at {store.root}")
        return _ExitCode.OK
    if not args.yes:
        _eprint(
            f"refusing to revoke: this will delete EVERY cert + key under "
            f"{store.root}.\n  re-run with --yes to confirm.",
        )
        return _ExitCode.REFUSED
    shutil.rmtree(store.root)
    print(f"✓ removed {store.root}. Run `wolf-cert init` to regenerate.")
    return _ExitCode.OK


# ──────────────────────────────────────────────────────────────────────────
# Argparse wiring
# ──────────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wolf-cert",
        description=(
            "Manage Wolf's self-signed CA and leaf certificates. "
            "Phase 5.4 of the build roadmap — see "
            "docs/decisions/ for the design ADR."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # `init`
    p_init = sub.add_parser("init", help="Generate CA + leaf certificates")
    _add_cert_dir(p_init)
    p_init.add_argument(
        "--years",
        type=int,
        default=100,
        help="Validity in years (default: 100 — the 'practical infinity' "
        "pattern; RFC 5280 forbids truly unlimited)",
    )
    p_init.add_argument(
        "--ca-cn",
        default="Wolf Root CA",
        help="Common Name on the CA certificate (default: 'Wolf Root CA')",
    )
    p_init.add_argument(
        "--ca-org",
        default="Wolf",
        help="Organization on the CA certificate (default: 'Wolf')",
    )
    p_init.add_argument(
        "--san-dns",
        action="append",
        default=[],
        metavar="HOSTNAME",
        help="Additional DNS SAN (repeatable). Auto-detected names "
        "from `discover_local_sans()` are always included.",
    )
    p_init.add_argument(
        "--san-ip",
        action="append",
        default=[],
        metavar="IP",
        help="Additional IP SAN (repeatable). Auto-detected addresses "
        "from `discover_local_sans()` are always included.",
    )
    p_init.set_defaults(func=cmd_init)

    # `status`
    p_status = sub.add_parser(
        "status",
        help="Show certificate metadata (subject, SANs, validity, fingerprint)",
    )
    _add_cert_dir(p_status)
    p_status.set_defaults(func=cmd_status)

    # `export-ca`
    p_export = sub.add_parser(
        "export-ca",
        help="Output the CA certificate for installation in OS/browser "
        "trust stores",
    )
    _add_cert_dir(p_export)
    p_export.add_argument(
        "--format",
        choices=["pem", "der"],
        default="pem",
        help="Output format (default: pem)",
    )
    p_export.add_argument(
        "--out",
        default=None,
        help="Write to this path; '-' or omitted writes to stdout",
    )
    p_export.set_defaults(func=cmd_export_ca)

    # `add-host`
    p_add = sub.add_parser(
        "add-host",
        help="Add a hostname or IP to the leaf certs' SubjectAlternativeName",
    )
    _add_cert_dir(p_add)
    p_add.add_argument("host", help="Hostname or IP address to add")
    p_add.add_argument(
        "--leaf",
        default="all",
        help="Which leaf to update ('all', 'server', 'dashboard', etc.)",
    )
    p_add.set_defaults(func=cmd_add_host)

    # `renew`
    p_renew = sub.add_parser(
        "renew",
        help="Reissue leaves (and optionally the CA) with fresh validity",
    )
    _add_cert_dir(p_renew)
    p_renew.add_argument(
        "--years",
        type=int,
        default=100,
        help="Validity in years for the reissued cert(s) (default: 100)",
    )
    p_renew.add_argument(
        "--leaf",
        default="all",
        help="Which leaf to reissue ('all', 'server', 'dashboard', etc.)",
    )
    p_renew.add_argument(
        "--ca",
        action="store_true",
        help="Also reissue the CA (regenerates the CA key — every leaf "
        "becomes untrusted until reissued against the new CA)",
    )
    p_renew.set_defaults(func=cmd_renew)

    # `revoke`
    p_revoke = sub.add_parser(
        "revoke",
        help="Delete every cert + key under <cert-dir>. Forces a re-init.",
    )
    _add_cert_dir(p_revoke)
    p_revoke.add_argument(
        "--yes",
        action="store_true",
        help="Confirm destructive deletion of the cert directory",
    )
    p_revoke.set_defaults(func=cmd_revoke)

    return parser


def _add_cert_dir(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cert-dir",
        default=str(DEFAULT_CERT_DIR),
        help=f"Cert directory root (default: {DEFAULT_CERT_DIR}/)",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    func = args.func
    return int(func(args))


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _resolve_sans(args: argparse.Namespace) -> tuple[list[str], list[str]]:
    """Auto-detected SANs (`discover_local_sans()`) merged with the
    user-supplied `--san-dns` / `--san-ip` flags. Dedupes preserving
    order — the discovered values come first."""
    dns, ips = discover_local_sans()
    for d in args.san_dns:
        if d not in dns:
            dns.append(d)
    for i in args.san_ip:
        if i not in ips:
            ips.append(i)
    return dns, ips


def _classify_host(host: str) -> tuple[list[str], list[str]]:
    """Decide whether `host` is a DNS name or an IP literal."""
    try:
        ipaddress.ip_address(host)
        return [], [host]
    except ValueError:
        return [host], []


def _select_leaves(store: CertStore, selector: str) -> list[str]:
    present = store.leaves_present()
    if selector == "all":
        return present
    return [n for n in present if n == selector]


def _existing_sans(cert: x509.Certificate) -> tuple[list[str], list[str]]:
    status = cert_status(cert)
    return list(status.san_dns), list(status.san_ip)


def _name_to_cn(cert: x509.Certificate) -> str:
    return cert_status(cert).subject_cn


def _kind_from_eku(cert: x509.Certificate) -> LeafKind:
    """Recover the LeafKind a leaf was originally issued with by reading
    its ExtendedKeyUsage extension. Used when reissuing — `add-host` and
    `renew` shouldn't accidentally change a server leaf into a client
    leaf or vice versa."""
    # Local import to keep the module's top-level imports minimal.
    from cryptography import x509
    from cryptography.x509.oid import ExtendedKeyUsageOID

    try:
        ext = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    except x509.ExtensionNotFound:
        return LeafKind.SERVER
    ekus = list(ext.value)
    has_server = ExtendedKeyUsageOID.SERVER_AUTH in ekus
    has_client = ExtendedKeyUsageOID.CLIENT_AUTH in ekus
    if has_server and has_client:
        return LeafKind.DUAL
    if has_client:
        return LeafKind.CLIENT
    return LeafKind.SERVER


def _print_status_block(label: str, status: CertStatus) -> None:
    """Render a single CertStatus to stdout in a fixed-width style.

    The expiry line shows both the absolute date and a human-readable
    "in N years, M months" so operators can spot near-expiry leaves
    at a glance during `wolf-cert status`."""
    print(f"=== {label} ===")
    print(f"  subject:     CN={status.subject_cn}")
    print(f"  issuer:      CN={status.issuer_cn}")
    print(f"  is CA:       {status.is_ca}")
    print(f"  SAN DNS:     {', '.join(status.san_dns) or '(none)'}")
    print(f"  SAN IP:      {', '.join(status.san_ip) or '(none)'}")
    print(f"  not before:  {status.not_before.isoformat()}")
    expires_relative = _humanize_until(status.not_after)
    print(f"  not after:   {status.not_after.isoformat()}  ({expires_relative})")
    print(f"  serial:      {status.serial:x}")
    print(f"  fingerprint: SHA256:{status.fingerprint_sha256}")
    print("")


def _humanize_until(when: datetime) -> str:
    """A coarse-grained "in N years, M months" formatter. Good enough
    for operator-readable status; not a precise duration library.

    Carry rule: 30-day months don't divide 365-day years cleanly
    (12*30 = 360, leaving 5 stray days that the arithmetic stuffs
    into the "months" slot). When the modulo math produces months
    >= 12, roll up into the year tally so we never print
    "99 years, 12 months" for what's effectively "100 years".
    """
    now = datetime.now(UTC)
    if when <= now:
        return "EXPIRED"
    delta = when - now
    total_days = delta.days
    years = total_days // 365
    months = (total_days % 365) // 30
    if months >= 12:
        years += 1
        months = 0
    y_word = f"{years} year{'s' if years != 1 else ''}"
    m_word = f"{months} month{'s' if months != 1 else ''}"
    d_word = f"{total_days} day{'s' if total_days != 1 else ''}"
    if years > 0 and months > 0:
        return f"in {y_word}, {m_word}"
    if years > 0:
        return f"in {y_word}"
    if months > 0:
        return f"in {m_word}"
    return f"in {d_word}"
