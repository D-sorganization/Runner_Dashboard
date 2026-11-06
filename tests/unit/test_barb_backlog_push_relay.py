"""Integration tests for barb_backlog_push_relay (#3711 extension, PR slice b)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import barb_backlog_push_lib as lib  # noqa: E402
import barb_backlog_push_relay as relay  # noqa: E402

BOT = "d-sorgclaudeagent[bot]"
TODAY = "2026-11-06"
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
VALID = {"repos": ["Runner_Dashboard"], "duration_min": 90, "lanes": 2}
BODY = "```backlog-push-request\n" + json.dumps(VALID) + "\n```"


class FakeGitHub:
    def __init__(self, issues: list[dict[str, Any]], editor: str | None = None) -> None:
        self.issues, self.editor, self.writes = issues, editor, []

    def open_requests(self, repo: str, label: str) -> list[dict[str, Any]]:
        assert label == lib.REQUEST_LABEL
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


def cfg(tmp_path: Path, cap: int = 2) -> lib.BacklogPushConfig:
    return lib.BacklogPushConfig(
        True,
        "http://hub.test",
        "tok",
        frozenset({BOT}),
        cap,
        tmp_path / "state.json",
        "D-sorganization/barbara",
    )


def test_relay_off_by_default_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAY_BACKLOG_PUSH_ENABLED", "0")
    assert relay.main([]) == 0


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
    first = relay.BacklogPushRelay(c, gh, state, launcher=fake_launcher, today=TODAY, now_fn=lambda: NOW)
    assert first.run()["started"] == 1
    assert ("label", 7, lib.STARTED) in gh.writes
    assert len(launched) == 1 and len(launched[0]["plan"]) == 3

    second = relay.BacklogPushRelay(c, gh, state, today=TODAY, now_fn=lambda: NOW)
    assert second.run()["done"] == 1
    assert state["active"] is None


def test_resource_gate_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relay, "hub_resource_refusal", lambda *_a, **_k: "Desk hub io_pressure 40.0%>=35%")
    gh = FakeGitHub([issue()])
    state = {"processed": {}, "push_starts": {}, "active": None}
    counts = relay.BacklogPushRelay(cfg(tmp_path), gh, state, today=TODAY, now_fn=lambda: NOW).run()
    assert counts["refused"] == 1


def test_dry_run_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relay, "hub_resource_refusal", lambda *_a, **_k: None)
    gh = FakeGitHub([issue()])
    state = {"processed": {}, "push_starts": {}, "active": None}
    relay.BacklogPushRelay(cfg(tmp_path), gh, state, today=TODAY, dry_run=True, now_fn=lambda: NOW).run()
    assert gh.writes == [] and state["active"] is None
