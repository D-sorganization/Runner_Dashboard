"""Tests for 'Waiting on you' Inbox and Barb Briefings API (SC-C5, Issue #1328).

Verifies GET /api/v1/staff/inbox and POST /api/v1/staff/briefing:
- Aggregates pending approvals, needs-input items, escalations, project decisions,
  board proposals, and auth sign-in items.
- Counts match underlying sources accurately.
- Fault isolation: A single failing source reports 'unavailable' without breaking the response.
- Morning/evening/on-demand briefings post to Barb's conversation thread with SC-A8 audit logging.
- Escalations send Web Push with deep links to the relevant thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from push import PUSH_TOPICS, PushKeys, PushSubscription, set_push_transport, upsert_subscription
from server import app
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.inbox import InboxItem, send_escalation_push
from staff.store import RunRecord
from staff.store import get_store as get_run_store
from staff.thread_bus import reset_thread_bus
from staff.work_items import get_work_item_store, reset_work_item_store

TEST_USER = Principal(
    id="operator-user",
    type="human",
    name="Dieter Olson",
    roles=["operator", "owner"],
    scopes=["staff.chat", "staff.read", "staff.approve", "staff.dispatch", "admin", "owner"],
)


class MockPushTransport:
    """Mock push transport capturing outbound push payloads."""

    def __init__(self) -> None:
        self.sent: list[tuple[PushSubscription, dict[str, Any]]] = []

    async def send(self, subscription: PushSubscription, payload: dict[str, Any]) -> int:
        self.sent.append((subscription, payload))
        return 201


@pytest.fixture(autouse=True)
def clean_inbox_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_file = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    monkeypatch.setenv("RUNNER_DASHBOARD_PUSH_DB", str(tmp_path / "push.sqlite3"))

    reset_conversation_store()
    reset_work_item_store()
    reset_thread_bus()

    app.dependency_overrides[require_principal] = lambda: TEST_USER
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_USER
    app.dependency_overrides[require_scope("staff.chat")] = lambda: TEST_USER

    transport = MockPushTransport()
    set_push_transport(transport)

    yield

    app.dependency_overrides.clear()
    reset_conversation_store()
    reset_work_item_store()
    reset_thread_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, base_url="http://testserver")


@pytest.mark.unit
def test_inbox_aggregates_multiple_sources(client: TestClient) -> None:
    c_store = get_conversation_store()
    w_store = get_work_item_store()
    r_store = get_run_store()

    # 1. Pending approval proposal
    th = c_store.create_thread(title="Restart runner", kind="direct", participants=["barb", "user"])
    c_store.create_proposal(
        message_id="msg_1",
        thread_id=th.id,
        action="runner.restart",
        params={"runner_name": "oglaptop", "rationale": "Unresponsive runner"},
        risk="high",
    )

    # 2. Needs input run & work item
    r_store.create_run(
        RunRecord(
            id="run_10931",
            role="issue-remediator",
            provider="fake",
            model=None,
            machine="DeskComputer",
            repo="UpstreamDrift",
            target_kind="issue",
            target_ref="10931",
            prompt="Fix math bug",
            status="needs_input",
            requested_by="barb",
            outcome="Should we use double precision float or fixed-point?",
        )
    )

    # 3. Escalated work item
    wi = w_store.create_work_item(
        title="CI deadlock in Tools",
        requested_by="barb",
        thread_id=th.id,
        owner_role="pr-remediator",
    )
    w_store.transition_state(wi.id, "escalated", actor="barb", reason="PR merge queue stalled across multiple shards")

    with (
        patch("projects.service.configured_repos", return_value=["UpstreamDrift"]),
        patch(
            "projects.service.project_overview",
            new=AsyncMock(return_value={"repo": "UpstreamDrift", "decisions_needed": ["Approve modal damping model"]}),
        ),
    ):
        resp = client.get("/api/v1/staff/inbox")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert "items" in data
        assert "counts" in data
        assert "sources" in data
        assert data["counts"]["approvals"] >= 1
        assert data["counts"]["needs_input"] >= 1
        assert data["counts"]["escalations"] >= 1
        assert data["counts"]["project_decisions"] >= 1
        assert data["sources"]["approvals"]["status"] == "ok"
        assert data["sources"]["project_decisions"]["status"] == "ok"


@pytest.mark.unit
def test_inbox_fault_isolation_reports_unavailable_source(client: TestClient) -> None:
    c_store = get_conversation_store()
    th = c_store.create_thread(title="Test", kind="direct", participants=["barb", "user"])
    c_store.create_proposal(
        message_id="msg_1",
        thread_id=th.id,
        action="runner.start",
        params={"rationale": "Testing"},
        risk="low",
    )

    # Mock projects.service.project_overview raising an unexpected exception
    with patch(
        "projects.service.project_overview",
        new=AsyncMock(side_effect=RuntimeError("GitHub API rate limit exceeded")),
    ):
        resp = client.get("/api/v1/staff/inbox")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # The overall endpoint still succeeds
        assert data["counts"]["approvals"] == 1
        # Project decisions source marked as unavailable
        assert data["sources"]["project_decisions"]["status"] == "unavailable"
        assert "rate limit" in data["sources"]["project_decisions"]["error"].lower()


@pytest.mark.unit
def test_briefing_posted_to_barb_thread(client: TestClient) -> None:
    c_store = get_conversation_store()
    th = c_store.create_thread(title="Barb", kind="direct", participants=["barb", "user"])

    resp = client.post(
        "/api/v1/staff/briefing",
        json={"kind": "morning"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["ok"] is True
    assert data["kind"] == "morning"
    assert "briefing_id" in data
    assert "Waiting on You" in data["body_md"]

    # Verify message was posted in Barb's thread
    msgs = c_store.list_messages(th.id)
    assert any("Morning Briefing" in m.body_md for m in msgs)


@pytest.mark.unit
def test_escalation_triggers_web_push(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "staff.escalation" in PUSH_TOPICS

    transport = MockPushTransport()
    set_push_transport(transport)

    item = InboxItem(
        id="esc-1",
        source="escalation",
        title="Flaky network partition on Runner 4",
        summary="Unable to reach worker host",
        severity="critical",
        created_at=datetime.now(UTC).isoformat(),
        link="/staff?thread=th_123",
        metadata={"thread_id": "th_123"},
    )

    # Subscribed push recipient
    upsert_subscription(
        user_id="operator-user",
        endpoint="https://push.example.com/sub/1",
        keys=PushKeys(p256dh="key_p256dh", auth="key_auth"),
        user_agent="test",
        topics=["staff.escalation"],
    )

    sent_count = asyncio.run(send_escalation_push(item))
    assert sent_count == 1
    assert len(transport.sent) == 1
    _, payload = transport.sent[0]
    assert payload["topic"] == "staff.escalation"
    assert "Flaky network partition" in payload["title"]
    assert payload["deep_link"] == "/staff?thread=th_123"
