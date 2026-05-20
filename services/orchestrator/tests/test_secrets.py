"""Tests for the encrypted-file secrets backend."""

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from wolf_common.errors import SecretBackendError
from wolf_secrets.file_backend import EncryptedFileBackend


@pytest.fixture
def backend(tmp_path: Path) -> EncryptedFileBackend:
    key = Fernet.generate_key().decode()
    return EncryptedFileBackend(file_path=str(tmp_path / "secrets.enc"), key=key)


async def test_get_missing_key_returns_none(backend: EncryptedFileBackend) -> None:
    assert await backend.get("does.not.exist") is None


async def test_set_and_get(backend: EncryptedFileBackend) -> None:
    await backend.set("my.key", "my-value")
    assert await backend.get("my.key") == "my-value"


async def test_overwrite(backend: EncryptedFileBackend) -> None:
    await backend.set("k", "v1")
    await backend.set("k", "v2")
    assert await backend.get("k") == "v2"


async def test_delete(backend: EncryptedFileBackend) -> None:
    await backend.set("k", "v")
    await backend.delete("k")
    assert await backend.get("k") is None


async def test_delete_nonexistent_is_silent(backend: EncryptedFileBackend) -> None:
    await backend.delete("nonexistent")  # must not raise


async def test_exists(backend: EncryptedFileBackend) -> None:
    await backend.set("present", "yes")
    assert await backend.exists("present") is True
    assert await backend.exists("absent") is False


async def test_multiple_keys(backend: EncryptedFileBackend) -> None:
    await backend.set("a", "1")
    await backend.set("b", "2")
    assert await backend.get("a") == "1"
    assert await backend.get("b") == "2"


async def test_invalid_key_raises(tmp_path: Path) -> None:
    with pytest.raises(SecretBackendError):
        EncryptedFileBackend(file_path=str(tmp_path / "s.enc"), key="not-a-valid-fernet-key")


async def test_encrypted_file_is_not_plaintext(backend: EncryptedFileBackend) -> None:
    """The file on disk must not contain the secret in plaintext."""
    await backend.set("sensitive", "super-secret-value")
    raw = backend._path.read_bytes()
    assert b"super-secret-value" not in raw
