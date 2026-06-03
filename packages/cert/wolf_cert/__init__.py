"""Wolf cert primitives — self-signed CA + leaf cert generation, PEM I/O,
and status parsing. Phase 5.4-a (the pure library); the `wolf-cert` CLI
(Phase 5.4-b) is the stateful shell over these primitives. The future
Wolf Knowledge Relay daemon also depends on this library to validate
wolf-server's server cert and consume its own client cert."""

from .authority import (
    DEFAULT_CA_KEY_SIZE,
    DEFAULT_LEAF_KEY_SIZE,
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

__all__ = [
    "DEFAULT_CA_KEY_SIZE",
    "DEFAULT_LEAF_KEY_SIZE",
    "DEFAULT_VALIDITY_DAYS",
    "CertStatus",
    "LeafKind",
    "cert_status",
    "discover_local_sans",
    "generate_ca",
    "read_cert_pem",
    "read_key_pem",
    "sign_leaf",
    "write_cert_pem",
    "write_key_pem",
]
