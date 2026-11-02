"""Shadow transport must accept a message before its dedupe key is consumed."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.rd_barb_notify import Notifier


@pytest.mark.parametrize("kind", ["update", "alert", "relay"])
def test_failed_shadow_post_can_retry_without_webhook(tmp_path: Path, kind: str) -> None:
    sender = Mock()
    poster = Mock(side_effect=OSError("offline"))
    notifier = Notifier(
        {"RD_BOB_SHADOW": "1", "RD_BOB_THREAD_ID": "th_fixture"},
        tmp_path / "state.json",
        sender,
        poster,
        role="bob",
    )

    def send():
        if kind == "relay":
            return notifier.relay("bob", "bob", "check", "th_fixture", allowed_pairs={"bob": "bob"})
        return notifier.notify(
            {
                "kind": kind,
                "urgency": "high",
                "summary": "fixture",
                "body": "",
                "dedupe_key": "fixture",
                "recommended_action": "Check the report.",
            }
        )

    assert send() == {"ok": False, "status": "not_sent", "error": "thread_send_failed"}
    poster.side_effect = None
    assert send() == {"ok": True, "status": "shadow"}
    assert send() == {"ok": True, "status": "deduped"}
    assert poster.call_count == 2
    sender.assert_not_called()


@pytest.mark.parametrize("has_thread,has_poster", [(False, True), (True, False)])
def test_shadow_requires_thread_transport(tmp_path: Path, has_thread: bool, has_poster: bool) -> None:
    config = {"RD_BOB_SHADOW": "1"}
    if has_thread:
        config["RD_BOB_THREAD_ID"] = "th_fixture"
    notifier = Notifier(config, tmp_path / "state.json", Mock(), Mock() if has_poster else None, role="bob")
    result = notifier.notify(
        {"kind": "update", "urgency": "normal", "summary": "fixture", "body": "", "dedupe_key": "fixture"}
    )
    assert result == {"ok": False, "status": "not_sent", "error": "thread_config_missing"}
    assert not (tmp_path / "state.json").exists()


def test_live_webhook_success_survives_optional_mirror_failure(tmp_path: Path) -> None:
    sender, poster = Mock(), Mock(side_effect=OSError("offline mirror"))
    notifier = Notifier(
        {
            "RD_BOB_WEBHOOK_URL": "https://fixture.invalid",
            "RD_BOB_WEBHOOK_KEY": "fixture",
            "RD_BOB_THREAD_ID": "th_fixture",
        },
        tmp_path / "state.json",
        sender,
        poster,
        role="bob",
    )
    message = {
        "kind": "alert",
        "urgency": "high",
        "summary": "fixture",
        "body": "",
        "dedupe_key": "fixture",
        "recommended_action": "Read report.",
    }
    assert notifier.notify(message) == {"ok": True, "status": "sent"}
    assert notifier.notify(message) == {"ok": True, "status": "deduped"}
    assert sender.call_count == 1 and poster.call_count == 1
