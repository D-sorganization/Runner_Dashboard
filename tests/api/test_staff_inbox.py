"""Tests for Staff 'Waiting on You' Inbox & Barb Briefings (SC-C5, Issue #1328).

Covers:
- GET /api/v1/staff/inbox aggregating 6 sources:
  1. Pending approvals (ActionProposalRecord state='proposed')
  2. Needs-input questions (runs/messages with needs_input)
  3. Escalations (runs with stalled/critical failure)
  4. Project decisions needed (Projects docs/project/STATUS.md)
  5. Board proposals awaiting owner (WorkItem role='board_secretary')
  6. Auth sign-ins required (classifier auth_expired items)
- Partial failure: one source failing marks it 'unavailable' while other sources render cleanly (HTTP 200)
- Barb briefing generation (morning, evening, /brief on-demand)
- Briefings posted to Barb thread on schedule and on-demand
- Web push notification emitted for escalations only with thread deep link
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from server import app
from staff.conversations import (
    get_conversation_store,
    reset_conversation_store,
)
from staff.inbox import (
    generate_barb_briefing,
    post_barb_briefing,
)
from staff.push_notifications import notify_escalation_push
from staff.store import RunRecord, get_store
from staff.thread_bus import reset_thread_bus
from staff.work_items import get_work_item_store, reset_work_item_store

TEST_PRINCIPAL = Principal(
    id="operator-user",
    type="human",
    name="Operator",
    roles=["operator"],
    scopes=["staff.chat", "staff.read", "staff.write"],
)


@pytest.fixture(autouse=True)
def clean_inbox_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    reset_conversation_store()
    reset_work_item_store()
    reset_thread_bus()

    app.dependency_overrides[require_principal] = lambda: TEST_PRINCIPAL
    app.dependency_overrides[require_scope("staff.chat")] = lambda: TEST_PRINCIPAL
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_PRINCIPAL
    app.dependency_overrides[require_scope("staff.write")] = lambda: TEST_PRINCIPAL

    yield
    app.dependency_overrides.clear()
    reset_conversation_store()
    reset_work_item_store()
    reset_thread_bus()


@pytest.fixture
def client():
    return TestClient(
        app,
        headers={"Host": "127.0.0.1", "Origin": "http://127.0.0.1"},
        raise_server_exceptions=False,
    )


def test_inbox_aggregates_all_six_sources(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Inbox collects from approvals, questions, escalations, projects, board, and auth sign-ins."""
    conv_store = get_conversation_store()
    wi_store = get_work_item_store()
    run_store = get_store()

    # 1. Approval
    th = conv_store.create_thread(title="PR Review", kind="direct", participants=["code-reviewer", "operator-user"])
    msg = conv_store.add_message(thread_id=th.id, author_kind="role", author="code-reviewer", body_md="Can I merge?")
    conv_store.create_proposal(
        message_id=msg.id,
        thread_id=th.id,
        action="github_merge",
        params={"repo": "Runner_Dashboard", "pr": 1420},
        risk="medium",
        principal="operator-user",
    )

    # 2. Needs-input question
    q_run = RunRecord(
        id="run-q-1",
        role="ad-hoc",
        provider="claude",
        model="claude-3-5-sonnet",
        machine="ControlTower",
        repo="Runner_Dashboard",
        target_kind="prompt",
        target_ref="main",
        prompt="Fix prompt",
        status="failed",
        failure_class="needs_input",
        retryable=True,
        remediation="Provide API key in settings",
        error="Agent paused asking for input",
    )
    run_store.create_run(q_run)

    # 3. Escalation
    esc_run = RunRecord(
        id="run-esc-1",
        role="maintenance",
        provider="codex",
        model="o3-mini",
        machine="ControlTower",
        repo="Runner_Dashboard",
        target_kind="prompt",
        target_ref="main",
        prompt="Maintenance check",
        status="failed",
        failure_class="stalled",
        retryable=False,
        remediation="Process unkillable, requires operator intervention",
        error="Job hung indefinitely",
    )
    run_store.create_run(esc_run)

    # 4. Project decisions needed (mocked projects.service)
    async def fake_project_overview(repo: str) -> dict[str, Any]:
        return {
            "repo": repo,
            "charter_present": True,
            "status_present": True,
            "decisions_needed": ["Migrate database to PostgreSQL or keep SQLite WAL?"],
        }

    monkeypatch.setattr("projects.service.configured_repos", lambda: ["Runner_Dashboard"])
    monkeypatch.setattr("projects.service.project_overview", fake_project_overview)

    # 5. Board proposal
    wi_store.create_work_item(
        title="[Board Proposal] Adopt rust-based micro-indexer",
        requested_by="board_secretary",
        thread_id=th.id,
        owner_role="board_secretary",
        links={"proposals": ["Adopt rust-based micro-indexer"]},
    )

    # 6. Auth sign-in required
    auth_run = RunRecord(
        id="run-auth-1",
        role="security-auditor",
        provider="claude",
        model="claude-3-5-sonnet",
        machine="OGLaptop",
        repo="Runner_Dashboard",
        target_kind="prompt",
        target_ref="main",
        prompt="Security scan",
        status="failed",
        failure_class="auth_expired",
        retryable=False,
        remediation="Run `claude auth login` on node OGLaptop",
        error="OAuth token expired",
    )
    run_store.create_run(auth_run)

    # Fetch inbox
    resp = client.get("/api/v1/staff/inbox")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert "items" in data
    assert "counts" in data
    assert "sources" in data

    counts = data["counts"]
    assert counts["approvals"] >= 1
    assert counts["questions"] >= 1
    assert counts["escalations"] >= 1
    assert counts["project_decisions"] >= 1
    assert counts["board_proposals"] >= 1
    assert counts["auth_signins"] >= 1

    # Check sources status
    sources = data["sources"]
    assert sources["approvals"]["status"] == "ok"
    assert sources["questions"]["status"] == "ok"
    assert sources["escalations"]["status"] == "ok"
    assert sources["project_decisions"]["status"] == "ok"
    assert sources["board_proposals"]["status"] == "ok"
    assert sources["auth_signins"]["status"] == "ok"

    # Verify item shapes
    categories = {it["category"] for it in data["items"]}
    assert "approval" in categories
    assert "question" in categories
    assert "escalation" in categories
    assert "project_decision" in categories
    assert "board_proposal" in categories
    assert "auth_signin" in categories

    for it in data["items"]:
        assert it["id"]
        assert it["title"]
        assert it["summary"]
        assert it["source"]
        assert it["severity"] in ("critical", "high", "medium", "low")


def test_inbox_partial_failure_marks_source_unavailable(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """When one source fails (e.g. project service error), that source is marked unavailable with 200 OK."""

    async def broken_projects(*args: Any, **kwargs: Any) -> list[str]:
        raise RuntimeError("GitHub API connection timeout")

    monkeypatch.setattr("projects.service.configured_repos", lambda: ["Runner_Dashboard"])
    monkeypatch.setattr("projects.service.project_overview", broken_projects)

    resp = client.get("/api/v1/staff/inbox")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # The failing source is marked unavailable
    assert data["sources"]["project_decisions"]["status"] == "unavailable"
    assert "GitHub API connection timeout" in data["sources"]["project_decisions"]["error"]

    # Other sources are still ok and returned
    assert data["sources"]["approvals"]["status"] == "ok"
    assert data["sources"]["questions"]["status"] == "ok"
    assert isinstance(data["items"], list)


def test_barb_briefing_generation(monkeypatch: pytest.MonkeyPatch):
    """generate_barb_briefing produces a structured markdown briefing with Waiting On You & Fleet sections."""
    inbox_payload = {
        "count": 2,
        "counts": {
            "approvals": 1,
            "escalations": 1,
            "questions": 0,
            "project_decisions": 0,
            "board_proposals": 0,
            "auth_signins": 0,
        },
        "items": [
            {
                "id": "app-1",
                "category": "approval",
                "title": "Approval required: merge PR #1420",
                "summary": "Context pane feature",
                "severity": "high",
                "action_url": "/staff?thread=th-1&proposal=p-1",
            },
            {
                "id": "esc-1",
                "category": "escalation",
                "title": "Escalation: Maintenance run stalled",
                "summary": "Disk compaction lock held",
                "severity": "critical",
                "action_url": "/staff?thread=th-2&run_id=run-esc",
            },
        ],
    }

    morning_brief = generate_barb_briefing(period="morning", inbox_data=inbox_payload)
    assert "Morning Briefing" in morning_brief
    assert "Waiting on You (2 items)" in morning_brief
    assert "merge PR #1420" in morning_brief
    assert "Maintenance run stalled" in morning_brief

    evening_brief = generate_barb_briefing(period="evening", inbox_data=inbox_payload)
    assert "Evening Briefing" in evening_brief


def test_post_barb_briefing_creates_thread_message():
    """post_barb_briefing finds or creates Barb's thread and appends the brief message."""
    conv_store = get_conversation_store()

    msg = post_barb_briefing(conv_store, period="morning", caller="scheduler")
    assert msg.id
    assert msg.author == "barb"
    assert msg.meta.get("kind") == "briefing"
    assert "Morning Briefing" in msg.body_md

    # Check thread was created with Barb
    th = conv_store.get_thread(msg.thread_id)
    assert th is not None
    assert "barb" in th.participants


def test_briefing_api_endpoint(client: TestClient):
    """POST /api/v1/staff/briefing posts a briefing and returns the message."""
    resp = client.post(
        "/api/v1/staff/briefing",
        json={"period": "on_demand"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert "message_id" in data
    assert "thread_id" in data
    assert "briefing_md" in data


@pytest.mark.asyncio
async def test_escalation_triggers_web_push(monkeypatch: pytest.MonkeyPatch):
    """notify_escalation_push calls send_push only for escalation items, never for non-escalations."""
    mock_send = AsyncMock(return_value={"sent": 1, "failed": 0, "purged": 0})
    monkeypatch.setattr("push.send_push", mock_send)

    escalation_item = {
        "id": "esc-123",
        "category": "escalation",
        "title": "Maintenance stalled on node OGLaptop",
        "summary": "Process hung for 30 minutes",
        "thread_id": "th-maint-1",
        "action_url": "/staff?thread=th-maint-1",
    }

    # 1. Escalation item sends push
    res = await notify_escalation_push(escalation_item)
    assert res is True
    assert mock_send.call_count == 1
    call_args = mock_send.call_args[0]
    assert call_args[0] == "staff.escalation"
    payload = call_args[1]
    assert payload["topic"] == "staff.escalation"
    assert payload["deep_link"] == "/staff?thread=th-maint-1"
    assert "Maintenance stalled" in payload["title"]

    mock_send.reset_mock()

    # 2. Non-escalation item does NOT send push
    normal_item = {
        "id": "app-456",
        "category": "approval",
        "title": "Merge PR",
        "summary": "Safe PR",
        "thread_id": "th-app-1",
        "action_url": "/staff?thread=th-app-1",
    }
    res2 = await notify_escalation_push(normal_item)
    assert res2 is False
    assert mock_send.call_count == 0


@pytest.mark.asyncio
async def test_brief_slash_command_execution():
    """ChatTurnRunner intercepts /brief command and replies with briefing."""
    from staff.chat import ChatTurnRunner

    conv_store = get_conversation_store()
    th = conv_store.create_thread(title="Barb", kind="direct", participants=["barb", "dieter"])
    user_msg = conv_store.add_message(thread_id=th.id, author_kind="user", author="dieter", body_md="/brief")
    placeholder = conv_store.add_message(
        thread_id=th.id, author_kind="role", author="barb", body_md="", delivery="pending"
    )

    runner = ChatTurnRunner(conv_store=conv_store)
    result = await runner.execute_turn(
        thread_id=th.id,
        user_message_id=user_msg.id,
        placeholder_id=placeholder.id,
        role_name="barb",
    )
    assert result.ok is True
    assert "Briefing" in result.reply
    assert "Waiting on You" in result.reply
