"""SECRET_KEY boot guard (hardening Gap 1).

The JWT signing key defaults to a placeholder that is public in the source
tree; a deployment that forgot to override it would let anyone forge JWTs
(including Superuser tokens). `Settings._validate_secret_key` fails closed on
the placeholder and on any key shorter than the documented 32-char minimum,
in every environment. These tests pin that contract.
"""

import pytest
from pydantic import ValidationError
from wolf_server.config import DEFAULT_SECRET_KEY, MIN_SECRET_KEY_LENGTH, Settings

# A well-formed override: unique and comfortably above the minimum length.
_STRONG_KEY = "s3cure-random-" + "x" * 40


def test_default_placeholder_is_rejected() -> None:
    """Booting with the public default placeholder must fail closed."""
    with pytest.raises(ValidationError) as exc:
        Settings(secret_key=DEFAULT_SECRET_KEY)
    message = str(exc.value)
    assert "SECRET_KEY" in message
    assert "public" in message  # the guided reason, not a bare "invalid"


def test_short_key_is_rejected() -> None:
    """A non-default but too-short key must fail closed with the length hint."""
    short = "x" * (MIN_SECRET_KEY_LENGTH - 1)
    with pytest.raises(ValidationError) as exc:
        Settings(secret_key=short)
    message = str(exc.value)
    assert str(MIN_SECRET_KEY_LENGTH) in message
    assert "at least" in message


def test_empty_key_is_rejected() -> None:
    """An empty key is caught by the length check (len 0 < minimum)."""
    with pytest.raises(ValidationError):
        Settings(secret_key="")


def test_key_at_minimum_length_is_accepted() -> None:
    """Exactly the minimum length, non-default, is the accepted boundary."""
    boundary = "a" * MIN_SECRET_KEY_LENGTH
    assert boundary != DEFAULT_SECRET_KEY
    settings = Settings(secret_key=boundary)
    assert settings.secret_key == boundary


def test_strong_key_is_accepted() -> None:
    """A comfortably-long unique key constructs normally."""
    settings = Settings(secret_key=_STRONG_KEY)
    assert settings.secret_key == _STRONG_KEY


def test_default_constant_is_at_least_minimum_length() -> None:
    """Guard against the placeholder being caught only by the length check.

    The placeholder is 40 chars (>= the minimum), so the explicit
    default-rejection branch — not the length branch — is what protects it.
    This pins that the placeholder rejection is doing real work.
    """
    assert len(DEFAULT_SECRET_KEY) >= MIN_SECRET_KEY_LENGTH
