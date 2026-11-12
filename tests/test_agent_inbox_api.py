"""API tests for the per-agent inbox endpoints (#3931 S3).

Clean-room: built from public docs/behavior descriptions only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("DASHBOARD_AGENT_INBOX_DB_PATH", str(tmp_path / "agent_inbox.sqlite3"))
    from fastapi.testclient import TestClient
    from identity import require_fleet_peer, require_scope
    from server import app

    write_dep = require_scope("coordination.write")
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    app.dependency_overrides[write_dep] = lambda: "test-writer"
    try:
        yield TestClient(app, raise_server_exceptions=False, headers={"X-Requested-With": "XMLHttpRequest"})
    finally:
        app.dependency_overrides.pop(require_fleet_peer, None)
        app.dependency_overrides.pop(write_dep, None)


def _send(
    client: Any, to: str = "codex-g", from_: str = "claude-a", body: str = "hi", requires_ack: bool = False
) -> dict[str, Any]:
    resp = client.post(
        "/api/v1/agent-inbox/send",
        json={"to": to, "from_": from_, "body": body, "requires_ack": requires_ack, "ack_deadline_seconds": 3600},
    )
    assert resp.status_code == 200
    return resp.json()


def test_list_on_empty_store_is_empty(client: Any) -> None:
    resp = client.get("/api/v1/agent-inbox/list", params={"agent": "codex-g"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_send_then_list_for_recipient(client: Any) -> None:
    msg = _send(client)
    listed = client.get("/api/v1/agent-inbox/list", params={"agent": "codex-g"})
    assert listed.status_code == 200
    assert listed.json() == [msg]

    other = client.get("/api/v1/agent-inbox/list", params={"agent": "grok-bot"})
    assert other.json() == []


def test_ack_marks_acked_and_excludes_from_default_list(client: Any) -> None:
    msg = _send(client, requires_ack=True)
    ack_resp = client.post("/api/v1/agent-inbox/ack", json={"message_id": msg["message_id"], "agent": "codex-g"})
    assert ack_resp.status_code == 200
    assert ack_resp.json()["acked_at"] is not None

    listed = client.get("/api/v1/agent-inbox/list", params={"agent": "codex-g"})
    assert listed.json() == []

    with_acked = client.get("/api/v1/agent-inbox/list", params={"agent": "codex-g", "include_acked": True})
    assert [m["message_id"] for m in with_acked.json()] == [msg["message_id"]]


def test_ack_unknown_message_is_409(client: Any) -> None:
    resp = client.post("/api/v1/agent-inbox/ack", json={"message_id": "not-a-real-id", "agent": "codex-g"})
    assert resp.status_code == 409


def test_ack_by_wrong_agent_is_409(client: Any) -> None:
    msg = _send(client, requires_ack=True)
    resp = client.post("/api/v1/agent-inbox/ack", json={"message_id": msg["message_id"], "agent": "grok-bot"})
    assert resp.status_code == 409


def test_overdue_lists_ack_required_past_deadline(client: Any) -> None:
    resp = client.post(
        "/api/v1/agent-inbox/send",
        json={
            "to": "codex-g",
            "from_": "claude-a",
            "body": "overdue",
            "requires_ack": True,
            "ack_deadline_seconds": 0.01,
        },
    )
    assert resp.status_code == 200
    msg = resp.json()
    import time

    time.sleep(0.05)
    overdue = client.get("/api/v1/agent-inbox/overdue")
    assert overdue.status_code == 200
    assert [m["message_id"] for m in overdue.json()] == [msg["message_id"]]
