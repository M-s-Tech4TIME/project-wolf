"""Tests for wolf-server's startup DB-reachability retry loop.

Phase 5.8-a — per ADR 0016 v3, Wolf systemd units are fully
independent (no After=/Requires=/Wants= between them), so a fresh
host boot can start wolf-server before wolf-database is accepting
connections. The lifespan hook's `_wait_for_database` handles
that with a backoff loop. These tests verify:

* Returns immediately when the DB is reachable on the first try.
* Retries with the configured backoff schedule when the DB throws.
* Surfaces the underlying error when the timeout is exceeded.

We mock the SQLAlchemy engine + asyncio.sleep so the tests are
deterministic and don't need a real Postgres.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from wolf_server.main import _wait_for_database


class _FakeConn:
    """Async-context-manager that records what was executed on it."""

    def __init__(self, raise_on_execute: Exception | None = None) -> None:
        self._raise = raise_on_execute
        self.execute = AsyncMock(side_effect=self._do_execute)

    async def _do_execute(self, _stmt: object) -> object:
        if self._raise is not None:
            raise self._raise
        return MagicMock()  # the SELECT 1 result

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass


def _engine_factory(
    *, fail_n_times: int, then_succeed: bool = True,
) -> tuple[MagicMock, list[int]]:
    """Build a mock create_async_engine that fails N times then OK.

    Returns the mock and a side-effect counter list so tests can
    assert how many engines were constructed.
    """
    counter = [0]

    def make_engine(_url: str) -> MagicMock:
        engine = MagicMock()
        attempt = counter[0]
        counter[0] += 1
        if attempt < fail_n_times:
            engine.connect = MagicMock(
                return_value=_FakeConn(
                    raise_on_execute=ConnectionRefusedError(
                        f"attempt {attempt}: refused",
                    ),
                ),
            )
        elif then_succeed:
            engine.connect = MagicMock(return_value=_FakeConn())
        else:
            engine.connect = MagicMock(
                return_value=_FakeConn(
                    raise_on_execute=ConnectionRefusedError("always fails"),
                ),
            )
        engine.dispose = AsyncMock()
        return engine

    return MagicMock(side_effect=make_engine), counter


# ─── happy path ─────────────────────────────────────────────────────────────


async def test_db_reachable_on_first_try_returns_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No retries needed; one engine constructed; one SELECT executed."""
    engine_fn, counter = _engine_factory(fail_n_times=0)
    monkeypatch.setattr(
        "sqlalchemy.ext.asyncio.create_async_engine", engine_fn,
    )
    sleep_mock = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep_mock)

    await _wait_for_database(backoff=(0.01,), timeout=5.0)

    assert counter[0] == 1  # one engine constructed
    sleep_mock.assert_not_awaited()  # no retries → no sleeps


# ─── retry then succeed ─────────────────────────────────────────────────────


async def test_retries_until_db_becomes_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three failed attempts then a success — four engines total,
    three sleeps between attempts."""
    engine_fn, counter = _engine_factory(fail_n_times=3)
    monkeypatch.setattr(
        "sqlalchemy.ext.asyncio.create_async_engine", engine_fn,
    )
    sleep_mock = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep_mock)

    # Use a tight backoff so the timeout is the long path here.
    await _wait_for_database(backoff=(0.001,), timeout=5.0)

    assert counter[0] == 4
    assert sleep_mock.await_count == 3


# ─── timeout exhausted ─────────────────────────────────────────────────────


async def test_raises_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When DB never comes back, we eventually give up and re-raise."""
    engine_fn, counter = _engine_factory(fail_n_times=999, then_succeed=False)
    monkeypatch.setattr(
        "sqlalchemy.ext.asyncio.create_async_engine", engine_fn,
    )
    # Real-ish sleeps so the elapsed accounting matches.
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    with pytest.raises(ConnectionRefusedError):
        # Each attempt fails immediately; timeout=0.05 with a 0.05
        # backoff means we hit the timeout after one retry.
        await _wait_for_database(backoff=(0.05,), timeout=0.05)

    # At least two engines (initial + at least one retry).
    assert counter[0] >= 2


# ─── backoff schedule cycles ────────────────────────────────────────────────


async def test_backoff_schedule_cycles_when_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the backoff tuple has 3 entries and we need 5 retries, the
    schedule cycles. itertools.cycle is the contract."""
    engine_fn, _counter = _engine_factory(fail_n_times=5)
    monkeypatch.setattr(
        "sqlalchemy.ext.asyncio.create_async_engine", engine_fn,
    )
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    await _wait_for_database(backoff=(0.001, 0.002, 0.003), timeout=10.0)

    # 5 retries means 5 sleeps. The first three are 0.001/0.002/0.003,
    # then the cycle restarts: 0.001, 0.002.
    assert sleeps == [0.001, 0.002, 0.003, 0.001, 0.002]
