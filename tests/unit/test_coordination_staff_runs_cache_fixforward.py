"""Fix-forward #4458 / merged PR #4461 — fleet staff_runs cache must not be repo-poisoned."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from coordination import hub_sessions, service, staff_view
from coordination.staff_runs_cache import (
    STAFF_RUNS_SNAPSHOT_MAX_AGE_S,
    record_staff_runs_snapshot,
    reset_staff_runs_cache,
    snapshot_staff_runs,
)


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    reset_staff_runs_cache()
    hub_sessions.reset_hub_sessions()


def test_filtered_staff_runs_does_not_record_fleet_cache() -> None:
    """Briefing-style filtered reads must not overwrite the global fleet snapshot."""
    asyncio.run(staff_view.staff_runs("RepoA"))
    runs, _, had = snapshot_staff_runs()
    assert had is False
    assert runs == []


def test_stale_staff_runs_snapshot_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    record_staff_runs_snapshot([{"id": "old", "repo": "Tools"}], [])
    now = 1_000_000.0
    monkeypatch.setattr("coordination.staff_runs_cache.time.monotonic", lambda: now + STAFF_RUNS_SNAPSHOT_MAX_AGE_S + 1)
    monkeypatch.setattr(
        "coordination.staff_runs_cache._recorded_at",
        now,
        raising=False,
    )
    runs, _, had = snapshot_staff_runs()
    assert had is False
    assert runs == []


def test_filtered_read_then_timed_out_fleet_sessions_does_not_serve_repo_only_cache(monkeypatch) -> None:
    """filtered staff_runs('A') then timed-out sessions(None) must not serve A-only fleet data."""
    record_staff_runs_snapshot(
        [
            {"id": "fleet-b", "repo": "RepoB"},
            {"id": "fleet-c", "repo": "RepoC"},
        ],
        [],
    )
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
        await asyncio.to_thread(gate.wait, 2)
        return ([{"id": "live"}], [])

    monkeypatch.setattr(service, "staff_runs", slow_staff)

    async def exercise() -> dict:
        await staff_view.staff_runs("RepoA")
        return await service.sessions(None)

    try:
        result = asyncio.run(exercise())
        assert result["staff_runs"] == [
            {"id": "fleet-b", "repo": "RepoB"},
            {"id": "fleet-c", "repo": "RepoC"},
        ]
        assert any("cached" in w.lower() for w in result.get("warnings", []))
    finally:
        gate.set()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            with service._SESSION_READ_LOCK:
                if "staff" not in service._SESSION_READS:
                    break
            time.sleep(0.01)