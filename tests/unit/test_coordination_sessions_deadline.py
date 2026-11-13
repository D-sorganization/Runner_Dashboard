"""Coordination sessions remain bounded when a backing read is slow (#2653)."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

from coordination import service, staff_view


def _wait_for_read_cleanup(key: str) -> None:
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        with service._SESSION_READ_LOCK:
            if key not in service._SESSION_READS:
                return
        time.sleep(0.005)
    raise AssertionError(f"shared {key} read did not finish")


def test_slow_board_read_preserves_staff_runs_and_returns_before_deadline(monkeypatch) -> None:
    gate = threading.Event()
    board_calls = []

    def slow_board(repo=None):
        board_calls.append(repo)
        gate.wait(2)
        return {
            "available": True,
            "complete": True,
            "sessions": [{"session": "late"}],
            "messages": [],
            "conflicts": [],
            "warnings": [],
        }

    async def fast_staff(repo=None):
        return ([{"id": "run-1", "repo": "Tools"}], [])

    monkeypatch.setattr(service, "SESSION_RESPONSE_DEADLINE_SECONDS", 0.05, raising=False)
    monkeypatch.setattr(service.board, "read_sessions", slow_board)
    monkeypatch.setattr(service, "staff_runs", fast_staff)
    try:
        result = asyncio.run(service.sessions("Tools"))
        assert result["available"] is False
        assert result["complete"] is False
        assert result["sessions"] == [] and result["messages"] == [] and result["conflicts"] == []
        assert result["staff_runs"] == [{"id": "run-1", "repo": "Tools"}]
        assert result["warnings"] == ["board sessions read timed out"]
        assert board_calls == [None]
    finally:
        gate.set()
        _wait_for_read_cleanup("board")


def test_slow_staff_read_preserves_board_sessions(monkeypatch) -> None:
    gate = threading.Event()
    board_data = {
        "available": True,
        "complete": True,
        "sessions": [{"session": "s1", "repo": "Tools"}],
        "messages": [],
        "conflicts": [],
        "warnings": [],
    }

    monkeypatch.setattr(service, "SESSION_RESPONSE_DEADLINE_SECONDS", 0.05, raising=False)
    monkeypatch.setattr(service.board, "read_sessions", lambda repo=None: board_data)

    def slow_active_runs():
        gate.wait(2)
        return []

    runner = SimpleNamespace(machine="Desk", store=SimpleNamespace(active_runs=slow_active_runs))
    monkeypatch.setattr(staff_view, "get_runner", lambda: runner)

    async def empty_peer_board(local):
        return {"running": [], "queued": []}

    monkeypatch.setattr(staff_view.staff_fleet, "aggregate_board", empty_peer_board)
    try:
        result = asyncio.run(service.sessions("Tools"))
        assert result["available"] is True and result["complete"] is True
        assert result["sessions"] == board_data["sessions"]
        assert result["staff_runs"] == []
        assert result["warnings"] == ["staff runs read timed out"]
    finally:
        gate.set()
        _wait_for_read_cleanup("staff")


def test_both_slow_reads_degrade_independently(monkeypatch) -> None:
    board_gate = threading.Event()
    staff_gate = threading.Event()
    monkeypatch.setattr(service, "SESSION_RESPONSE_DEADLINE_SECONDS", 0.05, raising=False)

    def slow_board(repo=None):
        board_gate.wait(2)
        return {"available": True, "complete": True, "sessions": [], "messages": [], "conflicts": [], "warnings": []}

    monkeypatch.setattr(service.board, "read_sessions", slow_board)

    async def slow_staff(repo=None):
        staff_gate.wait(2)
        return ([{"id": "late"}], [])

    monkeypatch.setattr(service, "staff_runs", slow_staff)
    try:
        result = asyncio.run(service.sessions(None))
        assert result["available"] is False and result["complete"] is False
        assert result["sessions"] == [] and result["staff_runs"] == []
        assert result["warnings"] == ["board sessions read timed out", "staff runs read timed out"]
    finally:
        board_gate.set()
        staff_gate.set()
        _wait_for_read_cleanup("board")
        _wait_for_read_cleanup("staff")


def test_cancelled_waiter_does_not_cancel_shared_read_and_late_failure_is_consumed(monkeypatch) -> None:
    gate = threading.Event()
    calls = []
    monkeypatch.setattr(service, "SESSION_RESPONSE_DEADLINE_SECONDS", 0.5, raising=False)

    def late_failure(repo=None):
        calls.append(1)
        gate.wait(2)
        raise RuntimeError("late board failure")

    monkeypatch.setattr(service.board, "read_sessions", late_failure)

    async def fast_staff(repo=None):
        return ([], [])

    monkeypatch.setattr(service, "staff_runs", fast_staff)

    async def exercise() -> None:
        first = asyncio.create_task(service.sessions(None))
        await asyncio.sleep(0.02)
        second = asyncio.create_task(service.sessions(None))
        await asyncio.sleep(0.02)
        first.cancel()
        try:
            await first
        except asyncio.CancelledError:
            pass
        gate.set()
        result = await second
        assert result["available"] is False
        assert result["reason"] == "board sessions read failed"

    try:
        asyncio.run(exercise())
        assert calls == [1]
    finally:
        gate.set()
        _wait_for_read_cleanup("board")


def test_repeated_timeouts_share_one_pending_read(monkeypatch) -> None:
    gate = threading.Event()
    calls = []
    monkeypatch.setattr(service, "SESSION_RESPONSE_DEADLINE_SECONDS", 0.03, raising=False)

    def slow_board(repo=None):
        calls.append(1)
        gate.wait(2)
        return {"available": True, "complete": True, "sessions": [], "messages": [], "conflicts": [], "warnings": []}

    monkeypatch.setattr(service.board, "read_sessions", slow_board)

    async def fast_staff(repo=None):
        return ([], [])

    monkeypatch.setattr(service, "staff_runs", fast_staff)
    try:
        first = asyncio.run(service.sessions(None))
        second = asyncio.run(service.sessions(None))
        assert first["available"] is False and second["available"] is False
        assert calls == [1]
    finally:
        gate.set()
        _wait_for_read_cleanup("board")
