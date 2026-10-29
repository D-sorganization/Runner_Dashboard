"""rd-barb dashboard inbox channel and multi-channel fan-out (RD #3075, Dieter's pick: dashboard plus email)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from staff import rd_barb_inbox as inbox
from staff.rd_barb_notify_health import NotifyHealth

from scripts import rd_barb_direct_notify as dn

T0 = 1_800_000_000.0
QUESTION = {
    "kind": "question",
    "summary": "Re-arm RD #1?",
    "body": "Checks green.",
    "urgency": "normal",
    "dedupe_key": "q-1",
    "options": [{"label": "Re-arm", "recommended": True}, {"label": "Wait"}],
    "recommendation_reason": "Required checks are green.",
}
ALERT = {"kind": "alert", "summary": "x", "urgency": "high", "dedupe_key": "a-1", "recommended_action": "Restart it."}


class Boom:
    name = "email"

    def send(self, msg: dict[str, Any]) -> None:
        raise dn.ChannelError("smtp_unreachable")


@pytest.fixture
def store(tmp_path: Path) -> inbox.DashboardInbox:
    return inbox.DashboardInbox(tmp_path / "inbox.json", clock=lambda: T0)


@pytest.fixture
def health(tmp_path: Path) -> NotifyHealth:
    return NotifyHealth(tmp_path / "health.json")


def test_delivered_question_is_stored_and_unacknowledged(store: inbox.DashboardInbox) -> None:
    store.add(QUESTION)
    [item] = store.unacknowledged()
    assert item["kind"] == "question" and item["summary"] == "Re-arm RD #1?"
    assert item["options"] == ["Re-arm", "Wait"] and item["acknowledged"] is False
    assert item["created_at"] == T0 and item["id"]


def test_same_dedupe_key_is_stored_once(store: inbox.DashboardInbox) -> None:
    store.add(QUESTION)
    store.add(QUESTION)
    assert len(store.unacknowledged()) == 1


def test_inbox_is_bounded_and_drops_the_oldest(tmp_path: Path) -> None:
    ticks = iter(range(100))
    small = inbox.DashboardInbox(tmp_path / "i.json", clock=lambda: float(next(ticks)), max_items=3)
    for n in range(5):
        small.add({**ALERT, "dedupe_key": f"a-{n}", "summary": f"alert {n}"})
    assert [i["summary"] for i in small.unacknowledged()] == ["alert 4", "alert 3", "alert 2"]


def test_acknowledged_items_leave_the_unread_list(store: inbox.DashboardInbox) -> None:
    store.add(QUESTION)
    item_id = store.unacknowledged()[0]["id"]
    assert store.acknowledge(item_id) is True
    assert store.unacknowledged() == [] and store.acknowledge("missing") is False


@pytest.mark.parametrize("body", ["token ghp_" + "a" * 36, "security-confidential detail"])
def test_secret_bearing_envelope_is_never_stored(store: inbox.DashboardInbox, tmp_path: Path, body: str) -> None:
    with pytest.raises(inbox.InboxRefused):
        store.add({**QUESTION, "body": body})
    assert store.unacknowledged() == [] and not (tmp_path / "inbox.json").exists()


def test_dashboard_channel_is_registered_and_delivers(
    tmp_path: Path, health: NotifyHealth, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    channel = dn.channel_from_config({"RD_BARB_DIRECT_CHANNEL": "dashboard"})
    assert dn.deliver(QUESTION, channel, health, clock=lambda: T0)["ok"] is True
    assert inbox.DashboardInbox(inbox.default_inbox_path()).unacknowledged()[0]["summary"] == "Re-arm RD #1?"


def test_secret_in_envelope_fails_delivery_by_code(
    tmp_path: Path, health: NotifyHealth, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    channel = dn.channel_from_config({"RD_BARB_DIRECT_CHANNEL": "dashboard"})
    result = dn.deliver({**QUESTION, "body": "ghp_" + "b" * 36}, channel, health, clock=lambda: T0)
    assert result["ok"] is False and result["error"] == "secret_refused"
    assert not inbox.default_inbox_path().exists()


def test_fan_out_partial_failure_is_not_delivered_and_names_the_failing_channel(
    tmp_path: Path, health: NotifyHealth, store: inbox.DashboardInbox
) -> None:
    channel = dn.FanOutChannel([dn.DashboardChannel(store), Boom()])
    result = dn.deliver(QUESTION, channel, health, clock=lambda: T0)
    assert result["ok"] is False and result["status"] == "not_sent"
    assert result["error"] == "email:smtp_unreachable"
    assert len(store.unacknowledged()) == 1  # the dashboard copy still landed
    status = health.status(now=T0)
    assert status["state"] == "failing"
    assert status["channels"]["email"]["state"] == "failing"
    assert status["channels"]["dashboard"]["state"] == "ok"


def test_config_list_builds_a_fan_out_and_dedupes_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    channel = dn.channel_from_config({"RD_BARB_DIRECT_CHANNEL": " dashboard , dashboard "})
    assert channel.name == "dashboard"
    assert dn.channel_from_config({"RD_BARB_DIRECT_CHANNEL": "dashboard,none"}).name == "dashboard+none"


def test_unknown_name_in_a_list_fails_closed(
    tmp_path: Path, health: NotifyHealth, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    channel = dn.channel_from_config({"RD_BARB_DIRECT_CHANNEL": "dashboard,carrier-pigeon"})
    result = dn.deliver(QUESTION, channel, health, clock=lambda: T0)
    assert result["ok"] is False and "unknown:unknown_channel" in result["error"]
    assert "carrier-pigeon" not in (tmp_path / "health.json").read_text(encoding="utf-8")
