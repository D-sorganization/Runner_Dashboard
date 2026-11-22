"""staff_runs() is decoupled from full aggregate_board() and bounded (#4619)."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest
from coordination import service, staff_view

from staff import fleet as staff_fleet


def _run(run_id: str, repo: str = "Tools", status: str = "running") -> dict[str, Any]:
    return {"id": run_id, "repo": repo, "status": status, "role": "dev", "provider": "claude", "machine": "m"}


class _Rec:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self.status = data["status"]

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)


@pytest.fixture(autouse=True)
def _local_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = SimpleNamespace(machine="Desk", store=SimpleNamespace(active_runs=lambda: [_Rec(_run("local-1"))]))
    monkeypatch.setattr(staff_view, "get_runner", lambda: runner)
    staff_fleet._LAST_GOOD_PEER_BOARD.clear()


def _forbid_aggregate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("staff_runs must not call aggregate_board")

    monkeypatch.setattr(staff_fleet, "aggregate_board", boom)


def test_budget_is_below_session_deadline() -> None:
    assert staff_view.STAFF_RUNS_PEER_BUDGET_SECONDS < service.SESSION_RESPONSE_DEADLINE_SECONDS


def test_staff_runs_does_not_use_aggregate_board_and_merges_peers(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_aggregate(monkeypatch)
    monkeypatch.setattr(staff_fleet, "peer_nodes", lambda: {"Peer": "http://peer"})

    async def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
        return {"machine": "Peer", "running": [_run("peer-1")], "queued": [_run("peer-q", status="queued")]}

    monkeypatch.setattr(staff_fleet, "get_json", fake_get)
    rows, warnings = asyncio.run(staff_view.staff_runs())
    assert {r["id"] for r in rows} == {"local-1", "peer-1", "peer-q"}
    assert warnings == []


def test_slow_peer_is_cut_off_at_budget_and_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_aggregate(monkeypatch)
    monkeypatch.setattr(staff_view, "STAFF_RUNS_PEER_BUDGET_SECONDS", 0.1)
    monkeypatch.setattr(staff_fleet, "peer_nodes", lambda: {"Slow": "http://slow"})

    async def hang(url: str, headers: dict[str, str]) -> dict[str, Any]:
        await asyncio.sleep(30)
        return {}

    monkeypatch.setattr(staff_fleet, "get_json", hang)
    started = time.monotonic()
    rows, warnings = asyncio.run(staff_view.staff_runs())
    assert time.monotonic() - started < 2.0
    assert [r["id"] for r in rows] == ["local-1"]
    assert len(warnings) == 1 and "Slow" in warnings[0] and "timed out" in warnings[0]


def test_slow_peer_serves_last_good_board_marked_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_aggregate(monkeypatch)
    monkeypatch.setattr(staff_view, "STAFF_RUNS_PEER_BUDGET_SECONDS", 0.1)
    monkeypatch.setattr(staff_fleet, "peer_nodes", lambda: {"Slow": "http://slow"})
    staff_fleet._LAST_GOOD_PEER_BOARD[("Slow", "http://slow")] = (
        time.monotonic() - 5.0,
        {"machine": "Slow", "running": [_run("cached-1")], "queued": []},
    )

    async def hang(url: str, headers: dict[str, str]) -> dict[str, Any]:
        await asyncio.sleep(30)
        return {}

    monkeypatch.setattr(staff_fleet, "get_json", hang)
    rows, warnings = asyncio.run(staff_view.staff_runs())
    assert {r["id"] for r in rows} == {"local-1", "cached-1"}
    assert len(warnings) == 1 and "Slow" in warnings[0] and "stale" in warnings[0]


def test_offline_peer_is_reported_not_silently_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_aggregate(monkeypatch)
    monkeypatch.setattr(staff_fleet, "peer_nodes", lambda: {"Down": "http://down"})

    async def refuse(url: str, headers: dict[str, str]) -> dict[str, Any]:
        raise ConnectionError("refused")

    monkeypatch.setattr(staff_fleet, "get_json", refuse)
    rows, warnings = asyncio.run(staff_view.staff_runs())
    assert [r["id"] for r in rows] == ["local-1"]
    assert len(warnings) == 1 and "Down" in warnings[0] and "unavailable" in warnings[0]


def test_repo_filter_still_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_aggregate(monkeypatch)
    monkeypatch.setattr(staff_fleet, "peer_nodes", lambda: {"Peer": "http://peer"})

    async def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
        return {"machine": "Peer", "running": [_run("peer-1", repo="Other")], "queued": []}

    monkeypatch.setattr(staff_fleet, "get_json", fake_get)
    rows, _ = asyncio.run(staff_view.staff_runs("tools"))
    assert [r["id"] for r in rows] == ["local-1"]


def test_sessions_endpoint_returns_peer_runs_within_deadline_when_peer_hangs(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: a hanging peer no longer empties staff_runs at the 5s deadline."""
    _forbid_aggregate(monkeypatch)
    monkeypatch.setattr(service, "SESSION_RESPONSE_DEADLINE_SECONDS", 1.0)
    monkeypatch.setattr(staff_view, "STAFF_RUNS_PEER_BUDGET_SECONDS", 0.2)
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
    monkeypatch.setattr(staff_fleet, "peer_nodes", lambda: {"Slow": "http://slow"})

    async def hang(url: str, headers: dict[str, str]) -> dict[str, Any]:
        await asyncio.sleep(30)
        return {}

    monkeypatch.setattr(staff_fleet, "get_json", hang)
    result = asyncio.run(service.sessions(None))
    assert [r["id"] for r in result["staff_runs"]] == ["local-1"]
    assert "staff runs read timed out" not in result["warnings"]
    assert any("Slow" in w for w in result["warnings"])
