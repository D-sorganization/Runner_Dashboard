"""Cached fallback when staff_runs read is slow (#4458)."""

from __future__ import annotations

import asyncio
import threading

import pytest
from coordination import hub_sessions, service
from coordination.staff_runs_cache import record_staff_runs_snapshot, reset_staff_runs_cache, snapshot_staff_runs


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    reset_staff_runs_cache()
    hub_sessions.reset_hub_sessions()


def test_snapshot_served_after_timeout(monkeypatch) -> None:
    reset_staff_runs_cache()
    record_staff_runs_snapshot([{"id": "cached-run", "repo": "Tools"}], [])
    gate = threading.Event()
    monkeypatch.setattr(service, "SESSION_RESPONSE_DEADLINE_SECONDS", 0.05, raising=False)
    monkeypatch.setattr(
        service.board,
        "read_sessions",
        lambda repo=None: {
            "available": True,
            "complete": True,
            "sessions": [],
            "messages": [],
            "conflicts": [],
            "warnings": [],
        },
    )

    async def slow_staff(repo=None):
        gate.wait(2)
        return ([{"id": "late"}], [])

    monkeypatch.setattr(service, "staff_runs", slow_staff)
    try:
        result = asyncio.run(service.sessions("Tools"))
        assert result["staff_runs"] == [{"id": "cached-run", "repo": "Tools"}]
        assert any("cached" in w.lower() for w in result["warnings"])
        assert any(s.get("session") == "staff:cached-run" for s in result["sessions"])
    finally:
        gate.set()
        from coordination import service as svc

        deadline = __import__("time").monotonic() + 1
        while __import__("time").monotonic() < deadline:
            with svc._SESSION_READ_LOCK:
                if "staff" not in svc._SESSION_READS:
                    break
            __import__("time").sleep(0.01)


def test_record_on_successful_read() -> None:
    reset_staff_runs_cache()
    record_staff_runs_snapshot([{"id": "r1"}], ["w"])
    runs, warnings, had_cache = snapshot_staff_runs()
    assert runs == [{"id": "r1"}] and had_cache is True