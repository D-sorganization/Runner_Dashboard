"""Barbara /backlog-push relay (#3711 extension)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import barb_backlog_push_lib as bpush  # noqa: E402
import barb_backlog_push_relay as relay  # noqa: E402
import barb_panel_relay as panel  # noqa: E402

BOT = "d-sorgclaudeagent[bot]"
TODAY = "2026-11-06"
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
VALID = {"repos": ["Runner_Dashboard"], "duration_min": 90, "lanes": 2}
BODY = "```backlog-push-request\n" + json.dumps(VALID) + "\n```"


def test_parse_and_bounds() -> None:
    assert bpush.parse_backlog_push_request(BODY)["duration_min"] == 90
    cfg = bpush.BacklogPushConfig(True, "http://h", "t", frozenset(), 2, Path("/tmp/x"), "D/x")
    bpush.validate_bounds({"duration_min": 30, "lanes": 1}, cfg)
    with pytest.raises(ValueError, match="duration_min"):
        bpush.validate_bounds({"duration_min": 29, "lanes": 1}, cfg)
    with pytest.raises(ValueError, match="lanes"):
        bpush.validate_bounds({"duration_min": 30, "lanes": 5}, cfg)


@pytest.mark.parametrize(
    "payload,match",
    [
        ({"repos": ["NoSuch"], "duration_min": 60, "lanes": 1}, "allowlisted"),
        ({"repos": ["Runner_Dashboard"], "duration_min": "60", "lanes": 1}, "integer"),
        ({"repos": ["Runner_Dashboard"], "duration_min": 60, "lanes": 1, "topic": "x"}, "unknown"),
        (
            {"repos": ["Runner_Dashboard\nDROP TABLE"], "duration_min": 60, "lanes": 1},
            "allowlisted",
        ),
    ],
)
def test_rejects_bad_params(payload: dict[str, Any], match: str) -> None:
    body = "```backlog-push-request\n" + json.dumps(payload) + "\n```"
    with pytest.raises(ValueError, match=match):
        bpush.parse_backlog_push_request(body)


def test_off_by_default_and_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = bpush.load_config({"RELAY_BACKLOG_PUSH_ENABLED": "0", "BARB_PANEL_RELAY_AUTHORS": BOT})
    assert not cfg.enabled
    monkeypatch.setenv("RELAY_BACKLOG_PUSH_ENABLED", "0")
    assert relay.main([]) == 0


class FakeGitHub:
    def __init__(self, issues: list[dict[str, Any]], editor: str | None = None) -> None:
        self.issues, self.editor, self.writes = issues, editor, []

    def open_requests(self, repo: str, label: str) -> list[dict[str, Any]]:
        assert label == bpush.REQUEST_LABEL
        return self.issues

    def snapshot(self, repo: str, number: int) -> dict[str, Any]:
        issue = next(i for i in self.issues if i["number"] == number)
        return {"author": issue["user"]["login"], "editor": self.editor, "body": issue["body"]}

    def comment(self, repo: str, number: int, body: str) -> None:
        self.writes.append(("comment", number, body))

    def label(self, repo: str, number: int, name: str) -> None:
        self.writes.append(("label", number, name))


def issue(number: int = 7, **extra: Any) -> dict[str, Any]:
    return {"number": number, "user": {"login": BOT}, "body": BODY, "labels": [], **extra}


def cfg(tmp_path: Path, cap: int = 2) -> bpush.BacklogPushConfig:
    return bpush.BacklogPushConfig(
        True,
        "http://hub.test",
        "tok",
        frozenset({BOT}),
        cap,
        tmp_path / "state.json",
        "D-sorganization/barbara",
    )


def test_daily_cap_and_single_flight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = {
        "processed": {},
        "push_starts": {TODAY: 2},
        "active": None,
    }
    gh = FakeGitHub([issue()])
    counts = relay.BacklogPushRelay(cfg(tmp_path, cap=2), gh, state, today=TODAY, now_fn=lambda: NOW).run()
    assert counts["refused"] == 1

    state = {
        "processed": {},
        "push_starts": {TODAY: 0},
        "active": {
            "issue": 99,
            "deadline_at": (NOW + timedelta(minutes=30)).isoformat(),
            "supervisor_session": "sup",
            "lane_sessions": ["l1"],
            "pids": {"supervisor": 999999},
        },
    }
    gh2 = FakeGitHub([issue(8)])

    def alive(pid: int) -> bool:
        return pid == 999999

    monkeypatch.setattr(relay, "_pid_alive", alive)
    counts2 = relay.BacklogPushRelay(cfg(tmp_path / "sf"), gh2, state, today=TODAY, now_fn=lambda: NOW).run()
    assert counts2 == dict.fromkeys(("started", "done", "refused", "skipped", "errors"), 0)
    assert gh2.writes == []


def test_starts_with_mock_launcher_and_finishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[dict[str, Any]] = []

    def fake_launcher(payload: dict[str, Any], log_dir: Path) -> dict[str, int]:
        launched.append(payload)
        return {"supervisor": 1000, "lane1": 1001, "lane2": 1002}

    monkeypatch.setattr(relay, "hub_resource_refusal", lambda *_a, **_k: None)
    monkeypatch.setattr(relay, "_pid_alive", lambda _pid: False)

    gh = FakeGitHub([issue()])
    state = {"processed": {}, "push_starts": {}, "active": None}
    c = cfg(tmp_path)
    relay_obj = relay.BacklogPushRelay(
        c,
        gh,
        state,
        launcher=fake_launcher,
        today=TODAY,
        now_fn=lambda: NOW,
    )
    assert relay_obj.run()["started"] == 1
    assert ("label", 7, bpush.STARTED) in gh.writes
    assert len(launched) == 1 and len(launched[0]["plan"]) == 3
    assert "Sidekick packaging" in launched[0]["plan"][0]["prompt"]
    assert "ignore any Barbara issue body" in launched[0]["plan"][1]["prompt"]

    # Reap on next pass (processes "dead")
    relay2 = relay.BacklogPushRelay(c, gh, state, today=TODAY, now_fn=lambda: NOW)
    assert relay2.run()["done"] == 1
    assert state["active"] is None
    assert ("label", 7, bpush.DONE) in gh.writes


def test_resource_gate_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relay, "hub_resource_refusal", lambda *_a, **_k: "Desk hub io_pressure 40.0%>=35%")
    gh = FakeGitHub([issue()])
    state = {"processed": {}, "push_starts": {}, "active": None}
    counts = relay.BacklogPushRelay(cfg(tmp_path), gh, state, today=TODAY, now_fn=lambda: NOW).run()
    assert counts["refused"] == 1
    assert any("io_pressure" in body for kind, _, body in gh.writes if kind == "comment")


def test_dry_run_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relay, "hub_resource_refusal", lambda *_a, **_k: None)
    gh = FakeGitHub([issue()])
    state = {"processed": {}, "push_starts": {}, "active": None}
    relay.BacklogPushRelay(cfg(tmp_path), gh, state, today=TODAY, dry_run=True, now_fn=lambda: NOW).run()
    assert gh.writes == [] and state["active"] is None


def test_build_launch_plan_never_includes_issue_body() -> None:
    sup, lanes = bpush.session_names(1, 1, NOW)
    plan = bpush.build_launch_plan(VALID, sup, lanes)
    joined = "\n".join(p["prompt"] for p in plan)
    assert "DROP TABLE" not in joined and "Runner_Dashboard" in joined


def test_hub_resource_disk_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(_method: str, _url: str, _token: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"disk": {"pressure": {"status": "critical", "reasons": ["pool full"]}}}

    monkeypatch.setattr(panel, "_request", fake_get)
    assert "disk-pressure" in (bpush.hub_resource_refusal("http://hub", "t") or "")
