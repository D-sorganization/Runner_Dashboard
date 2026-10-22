"""Agent bus hook helpers (#2155 slice 2): session start and pre-push heartbeat."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from fleet_client import FleetAPIError, FleetClient
from fleet_validators import default_session

import agent_bus_hooks as hooks


@pytest.fixture
def state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "agent-bus-state.json"
    monkeypatch.setattr(hooks, "state_file_path", lambda: path)
    return path


@pytest.mark.unit
def test_session_start_registers_and_persists_token(state_file: Path) -> None:
    client = FleetClient(base_url="http://127.0.0.1:9", agent="local", session="local-testhost-20261022-abcd")
    calls: list[tuple[str, str]] = []

    def fake_request(method: str, path: str, **kwargs):  # noqa: ANN003
        calls.append((method, path))
        if path == "/api/v1/agent/register":
            return {
                "session": client.session,
                "token": "abs_secret_token_value",
                "principal_id": f"fleet-session:{client.session}",
            }
        if path.startswith("/api/v1/agent/inbox"):
            return {"session": client.session, "messages": [{"id": "abm_1", "type": "request"}], "next_cursor": 1}
        raise AssertionError(path)

    with mock.patch.object(client, "request", side_effect=fake_request):
        summary = hooks.session_start(client, role="local", provider="cursor", node="Brick")

    assert summary["registered"] is True
    assert summary["inbox_count"] == 1
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["session"] == client.session
    assert saved["token"] == "abs_secret_token_value"
    assert "abs_secret" not in repr(calls)


@pytest.mark.unit
def test_session_start_noop_when_dashboard_unreachable(state_file: Path) -> None:
    client = FleetClient(base_url="http://127.0.0.1:9", agent="codex", session=default_session("codex"))

    def boom(*_a, **_k):  # noqa: ANN002
        raise FleetAPIError(0, {"error": "unreachable"})

    with mock.patch.object(client, "request", side_effect=boom):
        summary = hooks.session_start(client, role="codex", provider="codex", node="Hub")
    assert summary["offline"] is True
    assert not state_file.exists()


@pytest.mark.unit
def test_pre_push_heartbeats_with_saved_token(state_file: Path) -> None:
    sess = "issue-remediator-host-20261022-xyz"
    state_file.write_text(
        json.dumps({"session": sess, "token": "abs_saved", "role": "issue-remediator"}),
        encoding="utf-8",
    )
    client = FleetClient(base_url="http://127.0.0.1:9", token="abs_saved", session=sess, agent="issue-remediator")
    seen: list[str] = []

    def fake_request(method: str, path: str, **kwargs):  # noqa: ANN003
        seen.append(path)
        if path == "/api/v1/agent/heartbeat":
            return {"session": sess, "lease_expires_at": "2026-10-22T05:00:00Z"}
        if path.startswith("/api/v1/agent/inbox"):
            return {"session": sess, "messages": [], "next_cursor": 0}
        raise AssertionError(path)

    with mock.patch.object(client, "request", side_effect=fake_request):
        out = hooks.pre_push(client)
    assert out["heartbeat"] is True
    assert "/api/v1/agent/heartbeat" in seen
