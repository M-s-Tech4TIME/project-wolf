"""Encrypted-file secrets backend.

All secrets are stored in a single Fernet-encrypted JSON file on disk.
Suitable for development and small single-org deployments.  For production
MSSP use, replace with the OpenBao backend.

The file is read and written atomically (write to a temp file, then rename)
to avoid corruption on crash.  A file-level lock is held during writes.

Thread-safety: asyncio-compatible via asyncio.Lock.  Not multi-process safe
(use OpenBao for that).
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from wolf_common.errors import SecretBackendError


class EncryptedFileBackend:
    """Fernet-encrypted JSON file secrets backend."""

    def __init__(self, file_path: str, key: str) -> None:
        self._path = Path(file_path)
        self._lock = asyncio.Lock()
        try:
            self._fernet = Fernet(key.encode() if not key.endswith("=") else key.encode())
        except Exception as exc:
            msg = f"Invalid Fernet key for encrypted-file backend: {exc}"
            raise SecretBackendError(msg) from exc

    # ── Internal helpers ────────────────────────────────────────────────────

    def _load(self) -> dict[str, str]:
        """Read and decrypt the secrets file.  Returns empty dict if absent."""
        if not self._path.exists():
            return {}
        try:
            ciphertext = self._path.read_bytes()
            plaintext = self._fernet.decrypt(ciphertext)
            data: dict[str, str] = json.loads(plaintext)
            return data
        except InvalidToken as exc:
            msg = "Secrets file decryption failed — wrong key or corrupted file"
            raise SecretBackendError(msg) from exc
        except Exception as exc:
            msg = f"Failed to read secrets file at {self._path}: {exc}"
            raise SecretBackendError(msg) from exc

    def _save(self, data: dict[str, str]) -> None:
        """Encrypt and atomically write the secrets file."""
        try:
            plaintext = json.dumps(data).encode()
            ciphertext = self._fernet.encrypt(plaintext)
            # Atomic write: temp file + rename
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=self._path.parent, prefix=".wolf_secrets_")
            try:
                os.write(fd, ciphertext)
                os.fsync(fd)
            finally:
                os.close(fd)
            Path(tmp_path).rename(self._path)
        except Exception as exc:
            msg = f"Failed to write secrets file at {self._path}: {exc}"
            raise SecretBackendError(msg) from exc

    # ── Protocol implementation ─────────────────────────────────────────────

    async def get(self, key: str) -> str | None:
        async with self._lock:
            data = self._load()
            return data.get(key)

    async def set(self, key: str, value: str) -> None:
        async with self._lock:
            data = self._load()
            data[key] = value
            self._save(data)

    async def delete(self, key: str) -> None:
        async with self._lock:
            data = self._load()
            data.pop(key, None)
            self._save(data)

    async def exists(self, key: str) -> bool:
        async with self._lock:
            data = self._load()
            return key in data
