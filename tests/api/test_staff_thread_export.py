"""API tests for staff thread export and retention archival endpoints (Issue #1490).

Tests:
1. GET /api/v1/staff/threads/{id}/export?format=markdown returns 200 text/markdown
2. GET /api/v1/staff/threads/{id}/export?format=json returns 200 application/json
3. GET /api/v1/staff/threads/{id}/export.md and export.json aliases
4. GET /api/v1/staff/threads/{id}/export with unknown thread returns 404
5. POST /api/v1/staff/retention/sweep runs retention sweep and returns summary
6. GET /api/v1/staff/retention/status returns retention settings
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from server import app
from staff.conversations import ConversationStore, reset_conversation_store

TEST_ADMIN = Principal(
    id="admin:alice",
    type="human",
    name="Alice Admin",
    roles=["admin"],
    scopes=["staff.read", "staff.chat", "staff.admin"],
)


@pytest.fixture
def client_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setattr("staff.conversations.default_db_path", lambda: db_path)
    monkeypatch.setattr("staff.store.default_db_path", lambda: db_path)
    monkeypatch.setattr("staff.work_items.default_db_path", lambda: db_path)
    monkeypatch.setattr("staff.retention._default_archive_dir", lambda: tmp_path / "archives")

    reset_conversation_store()
    store = ConversationStore(db_path)
    monkeypatch.setattr("staff.conversations.get_conversation_store", lambda: store)

    app.dependency_overrides[require_principal] = lambda: TEST_ADMIN
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_ADMIN
    app.dependency_overrides[require_scope("staff.chat")] = lambda: TEST_ADMIN
    app.dependency_overrides[require_scope("staff.admin")] = lambda: TEST_ADMIN

    client = TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})

    yield {"client": client, "store": store, "headers": {}}
    app.dependency_overrides.clear()
    reset_conversation_store()


def test_export_thread_markdown(client_env: dict[str, Any]) -> None:
    client: TestClient = client_env["client"]
    store: ConversationStore = client_env["store"]
    headers: dict[str, str] = client_env["headers"]

    thread = store.create_thread(title="Project Kickoff", kind="direct", participants=["barb", "admin:alice"])
    store.add_message(
        thread_id=thread.id, author_kind="user", author="admin:alice", kind="text", body_md="Let's start."
    )
    store.add_message(thread_id=thread.id, author_kind="role", author="barb", kind="text", body_md="Routing to team.")

    resp = client.get(f"/api/v1/staff/threads/{thread.id}/export?format=markdown", headers=headers)
    assert resp.status_code == 200
    assert "text/markdown" in resp.headers["content-type"]
    assert f'filename="thread-{thread.id}.md"' in resp.headers.get("content-disposition", "")
    assert "# Thread: Project Kickoff" in resp.text
    assert "Let's start." in resp.text
    assert "Routing to team." in resp.text


def test_export_thread_json(client_env: dict[str, Any]) -> None:
    client: TestClient = client_env["client"]
    store: ConversationStore = client_env["store"]
    headers: dict[str, str] = client_env["headers"]

    thread = store.create_thread(title="Data Review", kind="direct", participants=["barb", "admin:alice"])
    store.add_message(
        thread_id=thread.id, author_kind="user", author="admin:alice", kind="text", body_md="Check stats."
    )

    resp = client.get(f"/api/v1/staff/threads/{thread.id}/export?format=json", headers=headers)
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    assert f'filename="thread-{thread.id}.json"' in resp.headers.get("content-disposition", "")

    payload = resp.json()
    assert payload["thread"]["id"] == thread.id
    assert payload["thread"]["title"] == "Data Review"
    assert len(payload["messages"]) == 1
    assert payload["messages"][0]["body_md"] == "Check stats."


def test_export_thread_extension_aliases(client_env: dict[str, Any]) -> None:
    client: TestClient = client_env["client"]
    store: ConversationStore = client_env["store"]
    headers: dict[str, str] = client_env["headers"]

    thread = store.create_thread(title="Extension Test", kind="direct", participants=["barb"])
    store.add_message(
        thread_id=thread.id,
        author_kind="role",
        author="barb",
        kind="text",
        body_md="Testing extension routes.",
    )

    resp_md = client.get(f"/api/v1/staff/threads/{thread.id}/export.md", headers=headers)
    assert resp_md.status_code == 200
    assert "Testing extension routes." in resp_md.text

    resp_json = client.get(f"/api/v1/staff/threads/{thread.id}/export.json", headers=headers)
    assert resp_json.status_code == 200
    assert resp_json.json()["thread"]["id"] == thread.id


def test_export_thread_not_found(client_env: dict[str, Any]) -> None:
    client: TestClient = client_env["client"]
    headers: dict[str, str] = client_env["headers"]

    resp = client.get("/api/v1/staff/threads/nonexistent-th/export", headers=headers)
    assert resp.status_code == 404
    assert "not found" in resp.text.lower()


def test_retention_sweep_endpoint(client_env: dict[str, Any]) -> None:
    client: TestClient = client_env["client"]
    headers: dict[str, str] = client_env["headers"]

    resp = client.post("/api/v1/staff/retention/sweep?retention_days=180", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "summary" in data
    assert data["summary"]["retention_days"] == 180


def test_retention_status_endpoint(client_env: dict[str, Any]) -> None:
    client: TestClient = client_env["client"]
    headers: dict[str, str] = client_env["headers"]

    resp = client.get("/api/v1/staff/retention/status", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["retention_days"] == 180
    assert "archive_dir" in data
