"""Tests for rd-barb slice 7 (#3075): board notifications, relay queue, 5-min banner, and answer routing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from routers import staff_rd_barb
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.rd_barb_inbox import DashboardInbox, InboxRefused, default_inbox_path
from staff.rd_barb_ledger import Ask, DecisionLedger, classify, default_ledger_path
from staff.rd_barb_policy import load_locked_policy
from staff.rd_barb_relay import (
    CorrelationRejectedError,
    RelayRetryQueue,
    default_relay_queue_path,
    ingest_board_post,
    register_question,
    route_answer,
)

_XHR = {"X-Requested-With": "XMLHttpRequest"}
RELAY_URL = "/api/v1/staff/rd-barb/relay-status"
INBOX_URL = "/api/v1/staff/rd-barb/inbox"
ANSWERS_URL = "/api/v1/staff/rd-barb/answers"

QUESTION = {
    "kind": "question",
    "summary": "Re-run stalled CI on PR #3500?",
    "body": "Run 991 timed out.",
    "urgency": "normal",
    "dedupe_key": "q-pr-3500-rerun",
    "options": [
        {"id": "rerun", "label": "Re-run CI", "recommended": True},
        {"id": "skip", "label": "Leave it", "recommended": False},
    ],
    "recommendation_reason": "Single transient network flake.",
}


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_runs.sqlite3"))
    reset_conversation_store()
    application = FastAPI()
    application.include_router(staff_rd_barb.router)
    yield application
    reset_conversation_store()


def _as(app: FastAPI, scopes: list[str]) -> TestClient:
    prin = Principal(id="grok-barb", type="bot", name="Grok Barb", roles=["bot"], scopes=scopes)
    app.dependency_overrides[require_principal] = lambda: prin
    return TestClient(app)


def test_board_post_deduped_in_inbox_with_unread_count_and_confidential_refusal(app: FastAPI) -> None:
    client = _as(app, ["fleet.read"])
    inbox = DashboardInbox(default_inbox_path())
    post = {"id": "board-msg-42", "from": "agy-i", "to": "*", "summary": "Lane agy-i armed #3567", "body": "All green"}

    assert ingest_board_post(post, inbox, source="fleet-board")["status"] == "added"
    assert ingest_board_post(post, inbox, source="RM#1576")["status"] == "deduped"
    inbox_body = client.get(INBOX_URL, headers=_XHR).json()
    assert inbox_body["unacknowledged"] == 1 and inbox_body["items"][0]["kind"] == "update"
    assert client.get(RELAY_URL, headers=_XHR).json()["unread_inbox_count"] == 1

    with pytest.raises(InboxRefused):
        ingest_board_post({"id": "sec-1", "summary": "security-confidential incident", "body": "secret"}, inbox)


def test_webhook_down_queues_with_backoff_delivers_once_and_5min_banner(app: FastAPI) -> None:
    client = _as(app, ["fleet.read"])
    now, delivered, webhook_up = [1000.0], [], [False]

    def sender(msg: dict) -> dict:
        if not webhook_up[0]:
            raise RuntimeError("webhook_503")
        delivered.append(msg)
        return {"ok": True}

    queue = RelayRetryQueue(default_relay_queue_path(), clock=lambda: now[0])
    assert queue.relay({**QUESTION, "dedupe_key": "q-sms", "channel": "sms"}, sender)["error"] == "sms_disallowed"
    assert queue.relay(QUESTION, sender)["status"] == "queued"
    assert queue.relay(QUESTION, sender)["status"] == "queued"
    assert queue.status()["queued_count"] == 1

    now[0] = 1002.0
    assert queue.flush_due(sender) == {"sent": 0, "remaining": 1}
    r_before = client.get(RELAY_URL, params={"now": 1299.0}, headers=_XHR).json()
    assert r_before["state"] == "failing" and r_before["banner_visible"] is False

    r_at_5m = client.get(RELAY_URL, params={"now": 1300.0}, headers=_XHR).json()
    assert r_at_5m["state"] == "degraded" and r_at_5m["banner_visible"] is True
    now[0] = 1300.0
    assert queue.flush_due(sender) == {"sent": 0, "remaining": 1}

    webhook_up[0], now[0] = True, 1320.0
    assert queue.flush_due(sender) == {"sent": 1, "remaining": 0} and len(delivered) == 1
    r_cleared = client.get(RELAY_URL, params={"now": 1320.0}, headers=_XHR).json()
    assert r_cleared["state"] == "ok" and r_cleared["banner_visible"] is False
    assert queue.relay(QUESTION, sender)["status"] == "deduped" and len(delivered) == 1


def test_answer_routing_completes_question_writes_ledger_and_never_lifts_hold(app: FastAPI) -> None:
    client = _as(app, ["fleet.read", "staff.dispatch"])
    t0 = datetime.now(UTC).replace(microsecond=0)
    store, policy = get_conversation_store(), load_locked_policy()
    ledger = DecisionLedger(path=default_ledger_path(), policy=policy, clock=lambda: t0)
    thread = store.create_thread(title="rd-threads:barb", kind="direct", role="rd-barb", created_by="rd-barb")

    ask_ok = Ask("rerun_ci", "Runner_Dashboard", "pr:3500")
    q_msg = register_question(
        store,
        thread_id=thread.id,
        correlation_id="corr-3500",
        summary="Re-run?",
        ask=ask_ok,
        expiry=t0 + timedelta(hours=1),
    )
    assert (
        client.post(ANSWERS_URL, json={"correlation_id": "unknown", "decision": "approve"}, headers=_XHR).status_code
        == 422
    )
    with pytest.raises(CorrelationRejectedError, match="expired_correlation"):
        route_answer(
            store, ledger, correlation_id="corr-3500", decision="approve", now=t0 + timedelta(hours=2), policy=policy
        )

    ok = client.post(
        ANSWERS_URL, json={"correlation_id": "corr-3500", "decision": "approve", "body": "Approved."}, headers=_XHR
    )
    assert ok.status_code == 200 and ok.json()["state"] == "completed"
    assert next(m for m in store.list_messages(thread.id) if m.id == q_msg.id).meta["state"] == "completed"
    assert len(ledger.records()) == 1 and ledger.records()[0]["decision"] == "approve"

    ask_hold = Ask("lift_hold", "Runner_Dashboard", "#2657")
    register_question(store, thread_id=thread.id, correlation_id="corr-hold-2657", summary="Touch #2657?", ask=ask_hold)
    hold_out = route_answer(store, ledger, correlation_id="corr-hold-2657", decision="approve", now=t0, policy=policy)
    assert hold_out["touches_locked_hold"] is True and hold_out["hold_lifted"] is False
    assert len(ledger.records()) == 2 and classify(ask_hold, ledger.records(), policy) == "never"
