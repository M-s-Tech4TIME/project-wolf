"""Tests for the immutable TenantContext.

Key invariants:
  - TenantContext is frozen — fields cannot be mutated after creation.
  - An invalid role raises immediately on construction.
  - All fields must be set; partial construction fails type-checking and at runtime.
"""

import uuid

import pytest
from wolf_server.tenancy.context import TenantContext


def _make_ctx(**overrides: object) -> TenantContext:
    defaults: dict[str, object] = {
        "tenant_id": uuid.uuid4(),
        "tenant_slug": "test-corp",
        "user_id": uuid.uuid4(),
        "user_email": "analyst@test.example",
        "role": "analyst",
        "session_id": str(uuid.uuid4()),
    }
    defaults.update(overrides)
    return TenantContext(**defaults)  # type: ignore[arg-type]


def test_context_is_frozen() -> None:
    ctx = _make_ctx()
    with pytest.raises((AttributeError, TypeError)):
        ctx.role = "admin"  # type: ignore[misc]


def test_valid_roles_are_accepted() -> None:
    for role in ("analyst", "approver", "admin", "superuser"):
        ctx = _make_ctx(role=role)
        assert ctx.role == role


def test_invalid_role_raises() -> None:
    with pytest.raises(ValueError, match="Invalid role"):
        _make_ctx(role="hacker")


def test_tenant_id_is_preserved() -> None:
    tid = uuid.uuid4()
    ctx = _make_ctx(tenant_id=tid)
    assert ctx.tenant_id == tid
