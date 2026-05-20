"""Secrets-backend abstraction for Wolf.

Use `get_backend(config)` to obtain the configured backend.
The encrypted-file backend works out of the box for development and small
single-org deployments. Wire OpenBao for production MSSP deployments.
"""

from wolf_secrets.file_backend import EncryptedFileBackend
from wolf_secrets.interface import SecretsBackend

__all__ = ["SecretsBackend", "EncryptedFileBackend", "get_backend"]


def get_backend(backend_type: str, **kwargs: object) -> "SecretsBackend":
    """Factory: return a SecretsBackend for the given backend_type.

    Supported values: "file".
    Additional backends (OpenBao, HashiCorp Vault) are added as later-phase adapters.
    """
    if backend_type == "file":
        file_path = str(kwargs.get("file_path", "/run/secrets/wolf_secrets.enc"))
        key = str(kwargs.get("key", ""))
        return EncryptedFileBackend(file_path=file_path, key=key)
    msg = f"Unknown secrets backend: {backend_type!r}"
    raise ValueError(msg)
