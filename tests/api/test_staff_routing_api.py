"""API tests for staff routing, decision, handoff, and override endpoints (SC-C2, Issue #1315)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from server import app
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.thread_bus import reset_thread_bus

TEST_USER = Principal(
    id="dieter",
    type="human",
    name="Dieter",
    roles=["owner", "operator"],
    scopes=["staff.chat", "staff.read", "staff.dispatch", "owner", "admin"],
)


@pytest.fixture(autouse=True)
def clean_conversations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_file = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    reset_conversation_store()
    reset_thread_bus()

    app.dependency_overrides[require_principal] = lambda: TEST_USER
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_USER
    app.dependency_overrides[require_scope("staff.chat")] = lambda: TEST_USER

    yield
    app.dependency_overrides.clear()
    reset_conversation_store()
    reset_thread_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, base_url="http://testserver")


def test_api_routing_decide_deterministic(client: TestClient) -> None:
    """POST /api/v1/staff/routing/decide returns deterministic decision."""
    resp = client.post(
        "/api/v1/staff/routing/decide",
        json={"text": "@librarian update documentation style guide"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["chosen_role"] == "librarian"
    assert data["confidence"] == 1.0
    assert data["mode"] == "explicit"
    assert data["needs_clarification"] is False


def test_api_routing_decide_clarification(client: TestClient) -> None:
    """Ambiguous prompt triggers clarification question rather than guessing."""
    resp = client.post(
        "/api/v1/staff/routing/decide",
        json={"text": "look at something weird"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["needs_clarification"] is True
    assert data["clarifying_question"] is not None


def test_api_routing_handoff(client: TestClient) -> None:
    """POST /api/v1/staff/routing/handoff dispatches handoff to target role."""
    store = get_conversation_store()
    source_th = store.create_thread(title="Auto Thread", kind="auto", participants=["barb", "dieter"])

    resp = client.post(
        "/api/v1/staff/routing/handoff",
        json={
            "text": "Review PR #1402 and fix failing tests",
            "source_thread_id": source_th.id,
            "target_role": "pr-remediator",
            "reason": "PR remediation requested",
            "confidence": 0.9,
            "mode": "pre_router",
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["target_role"] == "pr-remediator"
    assert data["target_thread_id"] != ""
    assert data["work_item_id"] != ""
    assert data["handoff_message_id"] != ""


def test_api_routing_override_and_feedback(client: TestClient) -> None:
    """POST /api/v1/staff/routing/override applies override and logs feedback."""
    store = get_conversation_store()
    source_th = store.create_thread(title="Auto Thread", kind="auto", participants=["barb", "dieter"])

    # 1. First trigger a handoff
    handoff_resp = client.post(
        "/api/v1/staff/routing/handoff",
        json={
            "text": "Fix typo in documentation",
            "source_thread_id": source_th.id,
            "target_role": "librarian",
            "reason": "Documentation task",
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert handoff_resp.status_code == 200, handoff_resp.text
    handoff_data = handoff_resp.json()

    # 2. Override to pragmatic-programmer
    override_resp = client.post(
        "/api/v1/staff/routing/override",
        json={
            "handoff_message_id": handoff_data["handoff_message_id"],
            "target_role": "pragmatic-programmer",
            "reason": "Contains code sample that needs refactoring",
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert override_resp.status_code == 200, override_resp.text
    ov_data = override_resp.json()
    assert ov_data["success"] is True
    assert ov_data["original_role"] == "librarian"
    assert ov_data["new_target_role"] == "pragmatic-programmer"

    # 3. List feedback
    fb_resp = client.get(
        "/api/v1/staff/routing/feedback",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert fb_resp.status_code == 200, fb_resp.text
    fb_list = fb_resp.json()["items"]
    assert len(fb_list) >= 1
    assert fb_list[0]["original_role"] == "librarian"
    assert fb_list[0]["override_role"] == "pragmatic-programmer"
