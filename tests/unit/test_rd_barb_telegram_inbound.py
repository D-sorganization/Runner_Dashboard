"""rd-barb Telegram inbound reply mapping: allowlist, correlation parsing, answer routing (RD #3805)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from staff.conversations import ConversationStore
from staff.rd_barb_inbox import DashboardInbox
from staff.rd_barb_ledger import Ask, DecisionLedger
from staff.rd_barb_policy import load_locked_policy
from staff.rd_barb_relay import register_question

from scripts import rd_barb_telegram_inbound as inbound

CFG = {"RD_BARB_TELEGRAM": "on", "RD_BARB_TELEGRAM_CHAT_ID": "999"}
ENV = {"TELEGRAM_BOT_TOKEN": "s3cret-bot-token"}  # pragma: allowlist secret


def _text_update(update_id: int, chat_id: str, text: str, *, reply_text: str = "") -> dict[str, Any]:
    message: dict[str, Any] = {"chat": {"id": chat_id}, "text": text}
    if reply_text:
        message["reply_to_message"] = {"text": reply_text}
    return {"update_id": update_id, "message": message}


def _callback_update(update_id: int, chat_id: str, data: str) -> dict[str, Any]:
    return {"update_id": update_id, "callback_query": {"data": data, "message": {"chat": {"id": chat_id}}}}


def _claude_provider(**_: Any) -> dict[str, Any]:
    return {"provider": "claude"}


def _no_provider(**_: Any) -> dict[str, Any]:
    return {"provider": None}


class FakeGet:
    def __init__(self, updates: list[dict[str, Any]]) -> None:
        self.updates = updates
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((url, params))
        return {"ok": True, "result": self.updates}


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        (_text_update(1, "999", "approve", reply_text="Re-arm? Ref: c_1"), ("c_1", "approve")),
        (_text_update(1, "999", "deny this one", reply_text="Ref: c_2"), ("c_2", "deny")),
        (_text_update(1, "999", "APPROVE\nRef: c_3"), ("c_3", "approve")),
        (_callback_update(1, "999", "deny:c_4"), ("c_4", "deny")),
        (_text_update(1, "999", "sounds good, no ref here"), None),
        (_text_update(1, "999", "approve", reply_text="no ref line"), None),
        (_callback_update(1, "999", "maybe:c_5"), None),
        (_callback_update(1, "999", "approve:"), None),
    ],
)
def test_parse_update(update: dict[str, Any], expected: tuple[str, str] | None) -> None:
    parsed = inbound.parse_update(update)
    assert (parsed is None) == (expected is None)
    if expected is not None:
        assert (parsed.correlation_id, parsed.decision) == expected


@pytest.fixture
def store(tmp_path: Path) -> ConversationStore:
    return ConversationStore(tmp_path / "conversations.sqlite3")


@pytest.fixture
def ledger(tmp_path: Path) -> DecisionLedger:
    return DecisionLedger(tmp_path / "decisions.jsonl", load_locked_policy())


@pytest.fixture
def inbox(tmp_path: Path) -> DashboardInbox:
    return DashboardInbox(tmp_path / "inbox.json")


def _poller(
    store: ConversationStore,
    ledger: DecisionLedger,
    inbox: DashboardInbox,
    http_get: FakeGet,
    *,
    cfg: dict[str, str] | None = None,
    provider_payload: Any = _no_provider,
) -> inbound.TelegramInboundPoller:
    return inbound.TelegramInboundPoller(
        cfg or CFG,
        environ=ENV,
        store=store,
        ledger=ledger,
        inbox=inbox,
        http_get=http_get,
        provider_payload=provider_payload,
    )


@pytest.mark.parametrize(
    ("cfg_overrides", "provider_payload"),
    [
        ({"RD_BARB_TELEGRAM": "off"}, _no_provider),
        ({"RD_BARB_TELEGRAM_CHANNELS_ENABLED": "on"}, _claude_provider),
    ],
)
def test_inactive_never_calls_getupdates(
    store: ConversationStore,
    ledger: DecisionLedger,
    inbox: DashboardInbox,
    cfg_overrides: dict[str, str],
    provider_payload: Any,
) -> None:
    http_get = FakeGet([])
    poller = _poller(store, ledger, inbox, http_get, cfg={**CFG, **cfg_overrides}, provider_payload=provider_payload)
    result = poller.poll_once(offset=5)
    assert result == {"ok": False, "reason": "inactive", "next_offset": 5}
    assert http_get.calls == []


def test_not_configured_without_token_or_chat_id(
    store: ConversationStore, ledger: DecisionLedger, inbox: DashboardInbox
) -> None:
    http_get = FakeGet([])
    poller = _poller(store, ledger, inbox, http_get, cfg={"RD_BARB_TELEGRAM": "on"})
    assert poller.poll_once()["reason"] == "telegram_not_configured"
    assert http_get.calls == []


def test_connect_failure_is_reported_without_raising(
    store: ConversationStore, ledger: DecisionLedger, inbox: DashboardInbox
) -> None:
    def boom(url: str, params: dict[str, Any]) -> dict[str, Any]:
        raise OSError("connection refused")

    poller = _poller(store, ledger, inbox, boom)  # type: ignore[arg-type]
    result = poller.poll_once(offset=3)
    assert result == {"ok": False, "reason": "telegram_connect_failed", "next_offset": 3}


def test_update_from_other_chat_is_dropped_and_not_routed(
    store: ConversationStore, ledger: DecisionLedger, inbox: DashboardInbox
) -> None:
    http_get = FakeGet([_text_update(10, "111", "approve", reply_text="Ref: c_other")])
    poller = _poller(store, ledger, inbox, http_get)
    result = poller.poll_once()
    assert result["dropped"] == 1 and result["routed"] == 0 and result["noted"] == 0
    assert result["next_offset"] == 11
    assert inbox.unacknowledged() == []


def test_matched_reply_routes_through_route_answer_and_completes_question(
    store: ConversationStore, ledger: DecisionLedger, inbox: DashboardInbox
) -> None:
    t0 = datetime.now(UTC)
    thread = store.create_thread(title="rd-threads:barb", kind="direct", role="rd-barb", created_by="rd-barb")
    register_question(
        store,
        thread_id=thread.id,
        correlation_id="corr-42",
        summary="Re-arm #3500?",
        ask=Ask("rerun_ci", "Runner_Dashboard", "pr:3500"),
        expiry=t0 + timedelta(hours=1),
    )
    http_get = FakeGet([_text_update(7, "999", "approve", reply_text="Re-arm? Ref: corr-42")])
    poller = _poller(store, ledger, inbox, http_get)

    result = poller.poll_once()

    assert result == {"ok": True, "routed": 1, "noted": 0, "dropped": 0, "next_offset": 8}
    completed = next(m for m in store.list_messages(thread.id) if m.meta.get("correlation_id") == "corr-42")
    assert completed.meta["state"] == "completed"
    assert len(ledger.records()) == 1 and ledger.records()[0]["decision"] == "approve"
    assert inbox.unacknowledged() == []


def test_unknown_correlation_id_becomes_inbox_note_not_dropped(
    store: ConversationStore, ledger: DecisionLedger, inbox: DashboardInbox
) -> None:
    http_get = FakeGet([_callback_update(2, "999", "deny:no-such-corr")])
    poller = _poller(store, ledger, inbox, http_get)

    result = poller.poll_once()

    assert result == {"ok": True, "routed": 0, "noted": 1, "dropped": 0, "next_offset": 3}
    items = inbox.unacknowledged()
    assert len(items) == 1 and "no-such-corr" in items[0]["summary"]
    assert ledger.records() == ()


def test_expired_correlation_id_becomes_inbox_note(
    store: ConversationStore, ledger: DecisionLedger, inbox: DashboardInbox
) -> None:
    t0 = datetime.now(UTC) - timedelta(hours=2)
    thread = store.create_thread(title="rd-threads:barb", kind="direct", role="rd-barb", created_by="rd-barb")
    register_question(
        store,
        thread_id=thread.id,
        correlation_id="corr-stale",
        summary="Old ask",
        ask=Ask("rerun_ci", "Runner_Dashboard", "pr:1"),
        expiry=t0 + timedelta(hours=1),
    )
    http_get = FakeGet([_callback_update(1, "999", "approve:corr-stale")])
    poller = _poller(store, ledger, inbox, http_get)

    result = poller.poll_once()

    assert result["routed"] == 0 and result["noted"] == 1
    assert "corr-stale" in inbox.unacknowledged()[0]["summary"]


def test_mixed_batch_counts_each_outcome_and_advances_offset_from_max_update_id(
    store: ConversationStore, ledger: DecisionLedger, inbox: DashboardInbox
) -> None:
    thread = store.create_thread(title="rd-threads:barb", kind="direct", role="rd-barb", created_by="rd-barb")
    register_question(
        store,
        thread_id=thread.id,
        correlation_id="corr-ok",
        summary="Ask",
        ask=Ask("a", "Runner_Dashboard", "s"),
        expiry=datetime.now(UTC) + timedelta(hours=1),
    )
    http_get = FakeGet(
        [
            _text_update(100, "999", "approve", reply_text="Ref: corr-ok"),
            _callback_update(101, "999", "deny:unknown-corr"),
            _text_update(102, "111", "approve", reply_text="Ref: corr-ok"),
        ]
    )
    poller = _poller(store, ledger, inbox, http_get)

    result = poller.poll_once(offset=50)

    assert result == {"ok": True, "routed": 1, "noted": 1, "dropped": 1, "next_offset": 103}


def test_run_loop_persists_offset_across_cycles(
    store: ConversationStore, ledger: DecisionLedger, inbox: DashboardInbox, tmp_path: Path
) -> None:
    offset_path = tmp_path / "offset.json"
    http_get = FakeGet([_text_update(5, "111", "approve")])  # other chat: dropped, but advances offset
    poller = _poller(store, ledger, inbox, http_get)

    inbound.run_loop(poller, offset_path=offset_path, max_iterations=1, sleep=lambda _s: None)

    assert inbound.load_offset(offset_path) == 6
    assert "offset" not in http_get.calls[0][1]

    inbound.run_loop(poller, offset_path=offset_path, max_iterations=1, sleep=lambda _s: None)
    assert http_get.calls[1][1]["offset"] == 6


def test_run_loop_sleeps_while_inactive_instead_of_busy_polling(
    store: ConversationStore, ledger: DecisionLedger, inbox: DashboardInbox, tmp_path: Path
) -> None:
    http_get = FakeGet([])
    poller = _poller(store, ledger, inbox, http_get, cfg={**CFG, "RD_BARB_TELEGRAM": "off"})
    slept: list[float] = []

    inbound.run_loop(poller, offset_path=tmp_path / "offset.json", max_iterations=2, sleep=slept.append)

    assert slept == [60.0, 60.0]
    assert http_get.calls == []
