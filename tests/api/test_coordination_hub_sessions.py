"""Hub session register and merged sessions list (#4458)."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from coordination import hub_sessions
from coordination_fake_rm import FakeRM, board, bot, install
from fastapi.testclient import TestClient
from staff import runner as runner_mod
from staff import store as store_mod

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_BOT = {"Authorization": "Bearer bot-token", **_XHR}


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    principals = {"bot-token": bot("agent-cursor")}
    yield from install(tmp_path, monkeypatch, principals, repos="Tools")


@pytest.fixture
def client(rm: FakeRM) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=("100.64.0.9", 51000))


@pytest.mark.unit
def test_register_hub_session_appears_in_sessions(rm: FakeRM, client: TestClient) -> None:
    hub_sessions.reset_hub_sessions()
    rm.respond("agent_communicate:list", board())
    body = {
        "session": "cursor-lane-a",
        "role": "cursor",
        "provider": "cursor",
        "machine": "cloud",
        "repo": "Tools",
        "target": "issue:9",
        "ttl_hours": 2,
    }
    resp = client.post("/api/coordination/sessions/register", json=body, headers=_BOT)
    assert resp.status_code == 200, resp.text
    sessions = client.get("/api/coordination/sessions").json()["sessions"]
    assert any(s["session"] == "cursor-lane-a" and s["source"] == "hub" for s in sessions)


@pytest.mark.unit
def test_running_staff_run_appears_in_sessions_not_only_staff_runs(rm: FakeRM, client: TestClient) -> None:
    hub_sessions.reset_hub_sessions()
    rm.respond("agent_communicate:list", board())
    store = runner_mod.get_runner().store
    store.create_run(
        store_mod.RunRecord(
            id="run-visible",
            role="ad-hoc",
            provider="claude",
            model=None,
            machine="node-b",
            repo="Tools",
            target_kind="prompt",
            target_ref="",
            prompt="hello",
            status="running",
            started_at="2026-11-15T10:00:00Z",
        )
    )  # fmt: skip
    payload = client.get("/api/coordination/sessions?repo=Tools").json()
    assert any(s.get("session") == "staff:run-visible" for s in payload["sessions"])
    assert payload["staff_runs"][0]["id"] == "run-visible"