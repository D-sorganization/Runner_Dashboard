"""rd-barb notify helper contract (docs/agents/rd-barb.md): validate, dedupe, digest, fail closed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts import rd_barb_notify as rn
from scripts import rd_threads_envelope as env

CFG = {"RD_BARB_WEBHOOK_URL": "https://hook.example/x", "RD_BARB_WEBHOOK_KEY": "k", "RD_BARB_THREAD_ID": "th_abc"}


def _question(**over: Any) -> dict[str, Any]:
    msg = {
        "kind": "question",
        "summary": "Re-arm RD #1?",
        "body": "Checks green.",
        "urgency": "normal",
        "dedupe_key": "q-1",
        "options": [{"label": "Re-arm", "recommended": True}, {"label": "Wait"}],
        "recommendation_reason": "Required checks are green.",
    }
    msg.update(over)
    return msg


def _alert(**over: Any) -> dict[str, Any]:
    msg = {
        "kind": "alert",
        "summary": "Burn guard tripped",
        "body": "",
        "urgency": "high",
        "dedupe_key": "a-1",
        "recommended_action": "Delete the two runner-mode variables.",
    }
    msg.update(over)
    return msg


def _fyi(key: str) -> dict[str, Any]:
    return {"kind": "fyi", "summary": f"merged {key}", "body": "", "urgency": "low", "dedupe_key": key}


class Rec:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        self.posts: list[tuple[str, str, str]] = []
        self.fail = fail

    def send(self, url: str, headers: Any, payload: dict[str, Any]) -> None:
        if self.fail:
            raise OSError("down")
        self.sent.append((url, dict(headers), payload))

    def post(self, tid: str, body: str, key: str) -> None:
        self.posts.append((tid, body, key))


@pytest.fixture
def rec() -> Rec:
    return Rec()


def _notifier(tmp_path: Path, rec: Rec, cfg: dict[str, str] | None = None, now: float = 1000.0) -> rn.Notifier:
    return rn.Notifier(CFG if cfg is None else cfg, tmp_path / "state.json", rec.send, rec.post, clock=lambda: now)


def test_valid_question_and_alert_pass_validation() -> None:
    assert rn.validate(_question()) == []
    assert rn.validate(_alert()) == []


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"options": None}, "options"),
        ({"options": [{"label": "Only", "recommended": True}]}, "at least 2"),
        ({"options": [{"label": "A"}, {"label": "B"}]}, "exactly one"),
        ({"options": [{"label": "A", "recommended": True}, {"label": "B", "recommended": True}]}, "exactly one"),
        ({"recommendation_reason": ""}, "recommendation_reason"),
        ({"recommendation_reason": "two\nlines"}, "recommendation_reason"),
        ({"kind": "ping"}, "kind"),
        ({"urgency": "asap"}, "urgency"),
        ({"dedupe_key": ""}, "dedupe_key"),
        ({"summary": ""}, "summary"),
    ],
)
def test_question_contract_violations_are_named(over: dict[str, Any], needle: str) -> None:
    assert any(needle in p for p in rn.validate(_question(**over)))


def test_alert_requires_recommended_action() -> None:
    assert any("recommended_action" in p for p in rn.validate(_alert(recommended_action=None)))


@pytest.mark.parametrize("msg", [_question(options=[{"label": "A"}, {"label": "B"}]), _alert(recommended_action="")])
def test_invalid_envelope_is_rejected_returned_and_not_sent(
    tmp_path: Path, rec: Rec, msg: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    result = _notifier(tmp_path, rec).notify(msg)
    assert result["ok"] is False and result["error"] == "invalid_envelope" and result["problems"]
    assert rec.sent == [] and "rejected" in caplog.text


def test_question_sends_immediately_with_key_header_and_mirrors_to_thread(tmp_path: Path, rec: Rec) -> None:
    assert _notifier(tmp_path, rec).notify(_question()) == {"ok": True, "status": "sent"}
    url, headers, payload = rec.sent[0]
    assert url == CFG["RD_BARB_WEBHOOK_URL"] and headers["Authorization"] == "Bearer k"
    assert payload["dedupe_key"] == "q-1"
    assert rec.posts[0][0] == "th_abc" and rec.posts[0][2] == "rd-barb-q-1"


def test_duplicate_within_window_is_deduped_then_resends_after(tmp_path: Path, rec: Rec) -> None:
    _notifier(tmp_path, rec).notify(_alert())
    assert _notifier(tmp_path, rec, now=1000.0 + 60).notify(_alert())["status"] == "deduped"
    assert _notifier(tmp_path, rec, now=1000.0 + rn.DEDUPE_WINDOW_S + 1).notify(_alert())["status"] == "sent"
    assert len(rec.sent) == 2


def test_fyi_is_batched_into_one_digest(tmp_path: Path, rec: Rec) -> None:
    n = _notifier(tmp_path, rec)
    assert n.notify(_fyi("f1"))["status"] == "queued_for_digest"
    n.notify(_fyi("f2"))
    n.notify(_fyi("f1"))
    assert rec.sent == []
    assert n.flush()["status"] == "sent"
    digest = rec.sent[0][2]
    assert digest["kind"] == "fyi" and len(digest["items"]) == 2
    assert n.flush()["status"] == "empty"


def test_missing_config_fails_closed_without_raising(
    tmp_path: Path, rec: Rec, caplog: pytest.LogCaptureFixture
) -> None:
    result = _notifier(tmp_path, rec, cfg={}).notify(_question())
    assert result == {"ok": False, "status": "not_sent", "error": "config_missing"}
    assert "config missing" in caplog.text
    # an unsent question is not marked seen, so it is retried once config exists
    assert _notifier(tmp_path, rec).notify(_question())["status"] == "sent"


def test_send_failure_keeps_digest_and_never_raises(tmp_path: Path) -> None:
    bad = Rec(fail=True)
    n = _notifier(tmp_path, bad)
    n.notify(_fyi("f1"))
    assert n.flush()["error"] == "send_failed"
    assert n._load_state()["digest"]


def test_load_config_env_overrides_file(tmp_path: Path) -> None:
    cfg_file = tmp_path / "rd-barb.env"
    cfg_file.write_text("# local only\nRD_BARB_WEBHOOK_URL=https://file\nRD_BARB_WEBHOOK_KEY=fk\n", encoding="utf-8")
    cfg = rn.load_config({"RD_BARB_CONFIG": str(cfg_file), "RD_BARB_WEBHOOK_URL": "https://env"})
    assert cfg["RD_BARB_WEBHOOK_URL"] == "https://env" and cfg["RD_BARB_WEBHOOK_KEY"] == "fk"
    assert rn.load_config({"RD_BARB_CONFIG": str(tmp_path / "missing")}) == {}


def test_cli_rejects_bad_json_with_exit_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "m.json"
    bad.write_text("{nope", encoding="utf-8")
    monkeypatch.setenv("RD_BARB_CONFIG", str(tmp_path / "none"))
    monkeypatch.setenv("RD_BARB_NOTIFY_STATE", str(tmp_path / "s.json"))
    monkeypatch.setattr(rn, "_fleet_thread_poster", lambda: None)
    assert rn.main(["send", "--json", str(bad)]) == 1
    assert "invalid_json" in capsys.readouterr().out


@pytest.mark.parametrize("msg", [_question(), _alert(), _fyi("f1")])
def test_valid_envelopes_also_pass_the_shared_rd_threads_contract(msg: dict[str, Any]) -> None:
    """notify builds through rd_threads_envelope (#3112): same shape, one validator."""
    shared = env.with_defaults({**msg, "kind": "update" if msg["kind"] == "fyi" else msg["kind"], "from": "rd-barb", "to": "barb"})
    assert rn.validate(msg) == [] and env.validate(shared) == []


def test_update_kind_is_accepted_as_the_canonical_alias_of_fyi(tmp_path: Path, rec: Rec) -> None:
    msg = _fyi("f1")
    msg["kind"] = "update"
    assert rn.validate(msg) == []
    assert _notifier(tmp_path, rec).notify(msg)["status"] == "queued_for_digest"


def test_recommendation_reason_is_required_and_must_be_one_line() -> None:
    """Required unconditionally since #3137; rd_barb_notify delegates to the shared envelope."""
    omitted = _question()
    del omitted["recommendation_reason"]
    assert any("recommendation_reason" in p for p in rn.validate(omitted))
    assert any("recommendation_reason" in p for p in rn.validate(_question(recommendation_reason="")))


def test_load_config_env_wins_for_every_rd_barb_key(tmp_path: Path) -> None:
    cfg_file = tmp_path / "rd-barb.env"
    cfg_file.write_text("RD_BARB_PROVIDER=claude\nRD_BARB_LAUNCH_CMD=bin/a {provider}\n", encoding="utf-8")
    env = {"RD_BARB_CONFIG": str(cfg_file), "RD_BARB_PROVIDER": "codex", "HOME": "/x", "RD_BARB_MODEL": ""}
    cfg = rn.load_config(env)
    assert cfg["RD_BARB_PROVIDER"] == "codex"
    assert cfg["RD_BARB_LAUNCH_CMD"] == "bin/a {provider}"
    assert "HOME" not in cfg and "RD_BARB_MODEL" not in cfg


def test_normalize_role_and_env_prefix_contract() -> None:
    assert (rn.normalize_role(), rn.normalize_role("barb"), rn.normalize_role("bob")) == ("rd-barb", "rd-barb", "bob")
    assert (rn.role_env_prefix(), rn.role_env_prefix("bob"), rn.role_env_prefix("usage-tracker")) == (
        "RD_BARB_",
        "RD_BOB_",
        "RD_USAGE_TRACKER_",
    )
    for bad in ("", "Bob", "../etc", "bob_1"):
        with pytest.raises(ValueError, match="role"):
            rn.normalize_role(bad)


def test_persona_notify_stamps_sender_role_and_keeps_barb_golden(tmp_path: Path, rec: Rec) -> None:
    res = rn.persona_notify(
        "bob", _question(), config=CFG, state_path=tmp_path / "bob.json", sender=rec.send, thread_poster=rec.post
    )
    assert res == {"ok": True, "status": "sent"}
    assert rec.sent[0][2]["sender_role"] == "bob"
    assert rec.posts[0][2] == "bob-q-1"
    _notifier(tmp_path, rec).notify(_question(dedupe_key="q-barb"))
    assert "sender_role" not in rec.sent[1][2] and rec.posts[1][2] == "rd-barb-q-barb"


def test_role_config_and_shadow_mode_suppress_webhook(tmp_path: Path, rec: Rec) -> None:
    cfg_file = tmp_path / "rd-bob.env"
    cfg_file.write_text("RD_BOB_PROVIDER=claude\nRD_BOB_THREAD_ID=th_bob\n", encoding="utf-8")
    cfg = rn.load_config({"RD_BOB_CONFIG": str(cfg_file), "RD_BOB_SHADOW": "1", **CFG}, role="bob")
    assert cfg["RD_BOB_PROVIDER"] == "claude" and cfg["RD_BOB_SHADOW"] == "1"
    res = rn.persona_notify(
        "bob", _question(), config=cfg, state_path=tmp_path / "bob.json", sender=rec.send, thread_poster=rec.post
    )
    assert res == {"ok": True, "status": "shadow"}
    assert rec.sent == [] and rec.posts[0][0] == "th_bob" and '"sender_role": "bob"' in rec.posts[0][1]

