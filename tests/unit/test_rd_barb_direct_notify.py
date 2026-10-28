"""rd-barb direct notify path: no Grok relay, and a failed delivery is visible (barbara #11 step 3, RD #3075)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
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


class FakeChannel:
    name = "fake"

    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self._error = error

    def send(self, msg: dict[str, Any]) -> None:
        if self._error is not None:
            raise self._error
        self.sent.append(msg)


@pytest.fixture
def health(tmp_path: Path) -> NotifyHealth:
    return NotifyHealth(tmp_path / "health.json")


def _deliver(msg: dict[str, Any], channel: Any, health: NotifyHealth) -> dict[str, Any]:
    return dn.deliver(msg, channel, health, clock=lambda: T0)


def test_unconfigured_channel_fails_visibly(health: NotifyHealth) -> None:
    result = _deliver(QUESTION, dn.channel_from_config({}), health)
    assert result == {"ok": False, "status": "not_sent", "error": "no_channel_configured", "health": "failing"}
    assert health.status(now=T0)["last_error"] == "no_channel_configured"


def test_success_sends_once_and_reads_ok(health: NotifyHealth) -> None:
    channel = FakeChannel()
    result = _deliver(QUESTION, channel, health)
    assert result == {"ok": True, "status": "sent", "health": "ok"} and channel.sent == [QUESTION]
    assert health.status(now=T0)["channel"] == "fake"


def test_unexpected_send_error_is_recorded_by_type_only(health: NotifyHealth, tmp_path: Path) -> None:
    result = _deliver(QUESTION, FakeChannel(ConnectionError("refused by relay.example")), health)
    assert result["ok"] is False and result["error"] == "send_failed:ConnectionError"
    stored = (tmp_path / "health.json").read_text(encoding="utf-8")
    assert "relay.example" not in stored and "Re-arm" not in stored


def test_invalid_envelope_is_rejected_before_any_send(health: NotifyHealth) -> None:
    channel = FakeChannel()
    result = _deliver({**QUESTION, "options": []}, channel, health)
    assert result["status"] == "rejected" and result["error"] == "invalid_envelope" and channel.sent == []
    assert health.status(now=T0)["state"] == "never_attempted"


def test_fyi_stays_on_the_digest_path(health: NotifyHealth) -> None:
    fyi = {"kind": "fyi", "summary": "s", "body": "", "urgency": "low", "dedupe_key": "f-1"}
    channel = FakeChannel()
    assert _deliver(fyi, channel, health)["error"] == "fyi_uses_digest" and channel.sent == []


def test_unknown_channel_name_fails_closed(health: NotifyHealth) -> None:
    channel = dn.channel_from_config({"RD_BARB_DIRECT_CHANNEL": "carrier-pigeon"})
    assert _deliver(QUESTION, channel, health)["error"] == "unknown_channel"


def test_grok_webhook_config_never_selects_a_channel() -> None:
    cfg = {"RD_BARB_WEBHOOK_URL": "https://hook.example/x", "RD_BARB_WEBHOOK_KEY": "k"}
    assert dn.channel_from_config(cfg).name == "none"


def test_deliver_never_raises_when_health_cannot_be_written(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("", encoding="utf-8")
    result = _deliver(QUESTION, FakeChannel(), NotifyHealth(blocked / "health.json"))
    assert result["ok"] is True and result["health"] == "unrecorded"


def test_cli_send_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("RD_BARB_CONFIG", str(tmp_path / "absent.env"))
    monkeypatch.delenv("RD_BARB_DIRECT_CHANNEL", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(QUESTION)))
    assert dn.main(["send", "--json", "-"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "no_channel_configured"
    assert dn.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "failing"
