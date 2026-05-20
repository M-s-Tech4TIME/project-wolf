"""Abstract interface for the Wolf secrets backend.

Every secrets backend implements this protocol.  The orchestrator and gateway
depend on this protocol, never on a concrete backend, so swapping backends
(encrypted file → OpenBao) requires zero changes to service code.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretsBackend(Protocol):
    """Async, key-value secrets store.

    Keys are namespaced by the caller (e.g. ``tenant/{tenant_id}/wazuh_api_key``).
    Values are always strings; callers that need structured data must
    serialise/deserialise themselves.

    All methods are async so that network-backed backends (OpenBao) do not block.
    """

    async def get(self, key: str) -> str | None:
        """Return the secret value for ``key``, or None if it does not exist."""
        ...

    async def set(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``, creating or overwriting."""
        ...

    async def delete(self, key: str) -> None:
        """Delete ``key``.  Silently succeeds if the key does not exist."""
        ...

    async def exists(self, key: str) -> bool:
        """Return True if ``key`` exists in the backend."""
        ...
