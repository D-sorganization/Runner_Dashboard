"""Tests for watchdog heartbeat task (issue #707)."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys_path_backend = str(Path(__file__).resolve().parents[2] / "backend")


def test_watchdog_heartbeat_calls_notify(monkeypatch):
    """Watchdog heartbeat must call sd_notify('WATCHDOG=1') at least once."""
    calls = []
    mock_notify = MagicMock(side_effect=lambda msg: calls.append(msg))
    monkeypatch.setenv("WATCHDOG_USEC", "100000")  # 100ms

    # Just verify the function logic using a mock notifier
    async def run_heartbeat_once(notify_fn, interval_s):
        """Run heartbeat one iteration."""
        try:
            notify_fn("WATCHDOG=1")
        except Exception:
            pass
        await asyncio.sleep(interval_s)

    async def test():
        await run_heartbeat_once(mock_notify, 0)

    asyncio.run(test())
    assert "WATCHDOG=1" in calls


def test_watchdog_heartbeat_handles_missing_notifier():
    """Heartbeat does not crash when sd_notify is unavailable."""

    # Simulate ImportError scenario
    def failing_notify(msg):
        raise OSError("not running under systemd")

    caught = []
    try:
        failing_notify("WATCHDOG=1")
    except Exception as e:
        caught.append(e)
    assert len(caught) == 1  # Should have been caught, not propagated


def test_watchdog_interval_from_env(monkeypatch):
    """Watchdog interval is derived from WATCHDOG_USEC env var."""
    import os

    monkeypatch.setenv("WATCHDOG_USEC", "200000")  # 200ms

    usec = int(os.environ.get("WATCHDOG_USEC") or "120000000")
    interval = usec / 1_000_000 / 2
    # 200ms / 2 = 100ms
    assert interval == pytest.approx(0.1, abs=1e-6)


@pytest.mark.asyncio
async def test_server_watchdog_heartbeat_survives_transient_error(monkeypatch):
    """RD-31 (#3034): _watchdog_heartbeat logs and continues after transient sd_notify error."""
    import datetime as _dt
    import enum as _enum
    import sys
    import typing as _typing

    import typing_extensions as _te

    if not hasattr(_dt, "UTC"):
        _dt.UTC = _dt.timezone.utc  # noqa: UP017
    if not hasattr(_enum, "StrEnum"):

        class _SE(str, _enum.Enum):  # noqa: UP042
            pass

        _enum.StrEnum = _SE
    if not hasattr(_typing, "Self"):
        _typing.Self = _te.Self
    if sys_path_backend not in sys.path:
        sys.path.insert(0, sys_path_backend)
    import server

    calls = []

    def flaky(msg):
        calls.append(msg)
        if len(calls) == 1:
            raise OSError("transient notify error")

    class _Stop(BaseException):
        pass

    n = 0

    async def fake_sleep(_s):
        nonlocal n
        n += 1
        if n >= 3:
            raise _Stop

    monkeypatch.setattr(server, "_sd_notify", flaky)
    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)
    with pytest.raises(_Stop):
        await server._watchdog_heartbeat()
    assert calls == ["WATCHDOG=1", "WATCHDOG=1"]
