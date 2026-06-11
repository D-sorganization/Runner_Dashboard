"""Tests for the in-process systemd watchdog heartbeat (A1).

Both the dashboard (`backend/server.py`) and the autoscaler
(`backend/runner_autoscaler.py`) must reset the systemd watchdog at regular
intervals or systemd will SIGABRT them after `WatchdogSec` elapses.

These tests verify that the heartbeat helper function exists, sends the
correct `WATCHDOG=1` payload, and respects its `WATCHDOG_USEC` environment
contract.
"""

from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# ---------------------------------------------------------------------------
# server.py heartbeat — async background task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_watchdog_task_emits_heartbeat(monkeypatch) -> None:
    """A1: the background task must call sd_notify('WATCHDOG=1') at least
    once per period when WATCHDOG_USEC is configured."""
    import server

    notifier = MagicMock()
    monkeypatch.setattr(server, "_sd_notify", notifier)
    monkeypatch.setenv("WATCHDOG_USEC", "200000")  # 200ms total => 100ms heartbeat

    # Run the loop briefly and cancel it. The loop should send at least one
    # heartbeat in this window.
    task = asyncio.create_task(server._systemd_watchdog_loop())
    await asyncio.sleep(0.35)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    payloads = [c.args[0] for c in notifier.call_args_list]
    assert any("WATCHDOG=1" in p for p in payloads), f"expected WATCHDOG=1 heartbeat; got {payloads!r}"


@pytest.mark.asyncio
async def test_server_watchdog_task_noops_without_systemd(monkeypatch) -> None:
    """A1: running outside systemd (no WATCHDOG_USEC, or _sd_notify=None) must
    not crash — it should exit the loop cleanly so local `python server.py`
    keeps working."""
    import server

    monkeypatch.setattr(server, "_sd_notify", None)
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)

    # The loop should return quickly when there is nothing to do.
    await asyncio.wait_for(server._systemd_watchdog_loop(), timeout=1.0)


@pytest.mark.asyncio
async def test_server_watchdog_task_survives_notifier_failure(monkeypatch) -> None:
    """A1: an exception from sd_notify (rare but possible during shutdown)
    must not kill the background task — the next iteration must still fire."""
    import server

    call_count = {"n": 0}

    def flaky(_payload: str) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated sd_notify failure")

    monkeypatch.setattr(server, "_sd_notify", flaky)
    monkeypatch.setenv("WATCHDOG_USEC", "200000")

    task = asyncio.create_task(server._systemd_watchdog_loop())
    await asyncio.sleep(0.35)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert call_count["n"] >= 2, "loop must continue past a single notifier failure"


# ---------------------------------------------------------------------------
# runner_autoscaler.py heartbeat — synchronous helper called per poll
# ---------------------------------------------------------------------------


def test_autoscaler_exports_watchdog_heartbeat() -> None:
    """A1: the autoscaler must expose a synchronous `_send_watchdog()` helper
    that the poll loop can call without going async."""
    import runner_autoscaler as ra

    assert hasattr(ra, "_send_watchdog"), "runner_autoscaler must define _send_watchdog()"


def test_autoscaler_watchdog_sends_payload(monkeypatch) -> None:
    """A1: when systemd watchdog is configured, the helper sends WATCHDOG=1."""
    import runner_autoscaler as ra

    notifier = MagicMock()
    monkeypatch.setattr(ra, "_sd_notify", notifier)
    monkeypatch.setenv("WATCHDOG_USEC", "120000000")

    ra._send_watchdog()

    payloads = [c.args[0] for c in notifier.call_args_list]
    assert payloads, "expected at least one sd_notify call"
    assert all("WATCHDOG=1" in p for p in payloads)


def test_autoscaler_watchdog_is_silent_when_systemd_absent(monkeypatch) -> None:
    """A1: outside systemd, the helper must be a no-op (never raise)."""
    import runner_autoscaler as ra

    monkeypatch.setattr(ra, "_sd_notify", None)
    # Should not raise even though there's no notifier configured.
    ra._send_watchdog()


def test_autoscaler_watchdog_swallows_notifier_failure(monkeypatch) -> None:
    """A1: a flaky notifier must not crash the autoscaler poll loop."""
    import runner_autoscaler as ra

    def boom(_payload: str) -> None:
        raise RuntimeError("simulated")

    monkeypatch.setattr(ra, "_sd_notify", boom)
    monkeypatch.setenv("WATCHDOG_USEC", "120000000")
    # Must not propagate.
    ra._send_watchdog()


# ---------------------------------------------------------------------------
# Dependency-free $NOTIFY_SOCKET fallback (issue #707 / OGLaptop 2026-06-09)
#
# The crash-loop root cause: the `systemd` python binding is not a declared
# dependency, so on the deployed host `_sd_notify is None` and the old watchdog
# silently no-op'd, letting systemd SIGABRT the process every WatchdogSec. The
# fallback writes the sd_notify datagram to $NOTIFY_SOCKET directly, so the
# heartbeat works with the binding absent.
# ---------------------------------------------------------------------------


def test_sd_notify_socket_returns_false_without_env(monkeypatch) -> None:
    """No $NOTIFY_SOCKET → best-effort no-op returning False (never raises)."""
    import runner_autoscaler as ra

    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert ra._sd_notify_socket("WATCHDOG=1") is False


def test_notify_systemd_falls_back_to_socket_when_binding_raises(monkeypatch) -> None:
    """When the systemd binding errors, _notify_systemd must try the socket."""
    import runner_autoscaler as ra

    def boom(_payload: str) -> None:
        raise RuntimeError("binding unavailable")

    captured: dict[str, str] = {}

    def fake_socket(state: str) -> bool:
        captured["state"] = state
        return True

    monkeypatch.setattr(ra, "_sd_notify", boom)
    monkeypatch.setattr(ra, "_sd_notify_socket", fake_socket)

    assert ra._notify_systemd("WATCHDOG=1") is True
    assert captured["state"] == "WATCHDOG=1"


@pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "AF_UNIX"),
    reason="AF_UNIX datagram sockets are unavailable on this platform",
)
def test_autoscaler_watchdog_delivers_over_notify_socket(monkeypatch, tmp_path) -> None:
    """End-to-end crash-loop fix: with the systemd binding absent
    (_sd_notify=None), _send_watchdog must still deliver WATCHDOG=1 by writing
    $NOTIFY_SOCKET — exactly the OGLaptop deployment condition."""
    import runner_autoscaler as ra

    sock_path = str(tmp_path / "notify.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    listener.bind(sock_path)
    listener.settimeout(2.0)
    try:
        monkeypatch.setattr(ra, "_sd_notify", None)
        monkeypatch.setenv("WATCHDOG_USEC", "120000000")
        monkeypatch.setenv("NOTIFY_SOCKET", sock_path)

        ra._send_watchdog()

        data, _addr = listener.recvfrom(64)
    finally:
        listener.close()
    assert data == b"WATCHDOG=1"
