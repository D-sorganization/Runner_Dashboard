"""The staff summary carries desktop CLI sessions registered on the coordination board (#2980)."""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from coordination import service as coord_service
from fastapi import FastAPI
from fastapi.testclient import TestClient
from staff import adapters as adapters_mod
from staff import fleet as fleet_mod
from staff import runner as runner_mod
from staff import store as store_mod

SESSION = {
    "session": "claude-rd-cli-20261026",
    "agent": "claude",
    "repo": "Runner_Dashboard",
    "issue": 2980,
    "branch": "claude/2980-x",
    "paths": ["backend/staff/summary_view.py"],
    "goals": {"k": "v"},
    "expires": "2026-10-26T19:00:00+00:00",
    "at": "2026-10-26T16:00:00+00:00",
    "source": "board",
}


def _board(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"available": True, "complete": True, "sessions": sessions, "messages": [], "conflicts": [], "warnings": []}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from identity import require_fleet_peer, require_orchestrator_peer  # noqa: PLC0415
    from routers import staff as staff_router  # noqa: PLC0415

    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "no-roles"))
    monkeypatch.setenv("STAFF_WORKTREES_ROOT", str(tmp_path / "wt"))
    monkeypatch.setenv("STAFF_SCHEDULE_STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(fleet_mod, "peer_nodes", lambda: {})
    fake = adapters_mod.ProviderAdapter(
        provider_id="claude", label="Fake claude", executable=sys.executable, argv=("-c", "pass"), json_lines=True
    )
    store_mod.reset_store()
    runner_mod.reset_runner()
    r = runner_mod.StaffRunner(
        store=store_mod.RunStore(tmp_path / "runs.sqlite3"), adapters={"claude": fake}, machine="Desk"
    )
    monkeypatch.setattr(runner_mod, "_runner", r)
    app = FastAPI()
    app.include_router(staff_router.router)
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    app.dependency_overrides[require_orchestrator_peer] = lambda: "test-orchestrator"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    store_mod.reset_store()
    runner_mod.reset_runner()


@pytest.mark.unit
def test_summary_lists_board_sessions(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coord_service.board, "read_sessions", lambda repo=None: _board([SESSION]))

    body = client.get("/api/staff/summary").json()

    assert body["board_sessions"] == [
        {
            "session": "claude-rd-cli-20261026",
            "agent": "claude",
            "repo": "Runner_Dashboard",
            "issue": 2980,
            "branch": "claude/2980-x",
            "expires": "2026-10-26T19:00:00+00:00",
        }
    ]
    assert "board_sessions_warning" not in body
    assert "holds" in body and "roles" in body  # existing keys untouched


@pytest.mark.unit
def test_unavailable_board_gives_empty_list_and_warning(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    unavailable = {**_board([]), "available": False, "reason": "RM checkout not found"}
    monkeypatch.setattr(coord_service.board, "read_sessions", lambda repo=None: unavailable)

    body = client.get("/api/staff/summary").json()

    assert body["board_sessions"] == []
    assert "RM checkout not found" in body["board_sessions_warning"]


@pytest.mark.unit
def test_slow_board_never_blocks_the_summary(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = threading.Event()

    def slow(repo=None):
        gate.wait(2)
        return _board([SESSION])

    monkeypatch.setattr(coord_service, "SESSION_RESPONSE_DEADLINE_SECONDS", 0.05)
    monkeypatch.setattr(coord_service.board, "read_sessions", slow)
    try:
        body = client.get("/api/staff/summary").json()
    finally:
        gate.set()

    assert body["board_sessions"] == []
    assert body["board_sessions_warning"] == "board sessions read timed out"
