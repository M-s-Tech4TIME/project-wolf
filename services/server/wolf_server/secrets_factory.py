"""App-level secrets backend accessor.

Reads the configured backend type and constructs the implementation.  The
return value is the SecretsBackend protocol type from wolf_secrets, so
callers depend only on the protocol.
"""

from functools import lru_cache

from wolf_secrets import SecretsBackend, get_backend

from wolf_server.config import Settings, get_settings


@lru_cache
def _build_backend(
    backend_type: str,
    file_path: str,
    file_key: str,
) -> SecretsBackend:
    return get_backend(backend_type, file_path=file_path, key=file_key)


def get_secrets_backend(settings: Settings | None = None) -> SecretsBackend:
    """Return the configured SecretsBackend.

    The backend is cached at the process level; tests that need a different
    backend should override via the FastAPI dependency-overrides mechanism.
    """
    s = settings or get_settings()
    return _build_backend(s.secrets_backend, s.secrets_file_path, s.secrets_file_key)
