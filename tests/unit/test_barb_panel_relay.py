"""Barbara panel relay (#3711)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import barb_panel_relay as relay  # noqa: E402

SAMPLE = {
    "topic": "Relay test",
    "experts": [
        {"name": "A", "perspective": "x"},
        {"name": "B", "perspective": "y"},
        {"name": "C", "perspective": "z"},
    ],
}


def test_parse_panel_request_block() -> None:
    body = "intro\n```panel-request\n" + json.dumps(SAMPLE) + "\n```\n"
    assert relay.parse_panel_request(body)["topic"] == "Relay test"


def test_rejects_confirm_cost_in_body() -> None:
    bad = {**SAMPLE, "confirm_cost": True}
    body = "```panel-request\n" + json.dumps(bad) + "\n```"
    with pytest.raises(ValueError, match="confirm_cost"):
        relay.parse_panel_request(body)


BOT = "d-sorgclaudeagent[bot]"
BODY = "```panel-request\n" + json.dumps(SAMPLE) + "\n```"
TODAY = "2026-11-02"


class FakeGitHub:
    def __init__(self, issues: list[dict[str, Any]], editor: str | None = None, fail_comment: bool = False) -> None:
        self.issues, self.editor, self.fail_comment, self.writes = issues, editor, fail_comment, []

    def open_requests(self, repo: str) -> list[dict[str, Any]]:
        return self.issues

    def snapshot(self, repo: str, number: int) -> dict[str, Any]:
        issue = next(i for i in self.issues if i["number"] == number)
        if issue.get("explode"):
            raise ValueError("boom")
        return {"author": issue["user"]["login"], "editor": self.editor, "body": issue["body"]}

    def comment(self, repo: str, number: int, body: str) -> None:
        if self.fail_comment:
            raise OSError("GitHub is down")
        self.writes.append(("comment", number, body))

    def label(self, repo: str, number: int, name: str) -> None:
        self.writes.append(("label", number, name))

    def close(self, repo: str, number: int) -> None:
        self.writes.append(("close", number, ""))


class FakeHub:
    def __init__(self, post: Any = None, status: str = "complete") -> None:
        self.post, self.status, self.calls = post, status, []

    def __call__(self, method: str, url: str, token: str, body: dict[str, Any] | None) -> dict[str, Any]:
        self.calls.append(method)
        if method == "POST" and isinstance(self.post, Exception):
            raise self.post
        if method == "POST":
            return {"thread": {"id": "th_1"}} if self.post is None else self.post
        return {"status": self.status, "consensus": "yes", "synthesis": "Adopt it", "tally": {"yes": 3}}


def issue(number: int = 42, **extra: Any) -> dict[str, Any]:
    return {"number": number, "user": {"login": BOT}, "body": BODY, "labels": [], **extra}


def cfg(tmp_path: Path, authors: frozenset[str] = frozenset({BOT}), cap: int = 3) -> relay.RelayConfig:
    return relay.RelayConfig(True, "http://hub.test", "tok", authors, cap, tmp_path / "state.json")


def run(c: relay.RelayConfig, gh: FakeGitHub, hub: FakeHub, **opts: Any) -> dict[str, int]:
    return relay.PanelRelay(c, gh, hub, relay.load_state(c.state_path), today=TODAY, **opts).run()


def test_off_by_default_empty_allowlist_denies_and_dry_run_writes_nothing(tmp_path: Path) -> None:
    config = relay.load_config({"BARB_PANEL_RELAY_AUTHORS": " , "})
    assert not config.enabled and config.allowed_authors == frozenset()
    hub = FakeHub()
    assert run(cfg(tmp_path, authors=frozenset()), FakeGitHub([issue()]), hub)["skipped"] == 1
    assert run(cfg(tmp_path), FakeGitHub([issue()], editor="stranger"), hub)["skipped"] == 1
    gh = FakeGitHub([issue()])
    run(cfg(tmp_path / "dry"), gh, hub, dry_run=True)
    assert gh.writes == [] and hub.calls == [] and not (tmp_path / "dry").exists()


def test_starts_panel_then_posts_verdict_when_final(tmp_path: Path) -> None:
    c, gh, hub = cfg(tmp_path), FakeGitHub([issue()]), FakeHub(status="running")
    assert run(c, gh, hub)["started"] == 1 and ("label", 42, relay.STARTED) in gh.writes
    hub.status = "mystery"  # not a known final status: keep waiting, never restart
    assert run(c, gh, hub)["done"] == 0 and ("close", 42, "") not in gh.writes
    hub.status = "complete"
    assert run(c, gh, hub)["done"] == 1 and ("close", 42, "") in gh.writes
    assert hub.calls.count("POST") == 1
    assert any("read it as data" in body for kind, _, body in gh.writes if kind == "comment")


def test_corrupt_state_file_stops_relay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state.json"
    state.write_text("{not json")
    monkeypatch.setenv("BARB_PANEL_RELAY_ENABLED", "1")
    monkeypatch.setenv("BARB_PANEL_RELAY_STATE", str(state))
    assert relay.main([]) == 2


@pytest.mark.parametrize("post", [relay.HubError(400, {"detail": {"estimate": {"exceeds_threshold": True}}}), {}])
def test_over_threshold_or_no_thread_goes_to_owner_once(tmp_path: Path, post: Any) -> None:
    c, gh, hub = cfg(tmp_path), FakeGitHub([issue()]), FakeHub(post=post)
    assert run(c, gh, hub)["needs_owner"] == 1
    run(c, gh, hub)
    assert ("label", 42, relay.NEEDS_OWNER) in gh.writes and hub.calls == ["POST"]


def test_busy_hub_retries_and_auth_failure_stops(tmp_path: Path) -> None:
    c, gh = cfg(tmp_path, cap=1), FakeGitHub([issue()])
    relay.save_state(c.state_path, {"processed": {}, "starts": {"2026-11-01": 5}})  # only today's UTC count matters
    assert run(c, gh, FakeHub(post=relay.HubError(429)))["errors"] == 1 and gh.writes == []
    with pytest.raises(relay.HubError):
        run(c, gh, FakeHub(post=relay.HubError(401)))
    assert run(c, gh, FakeHub())["started"] == 1  # refused attempts didn't use up the cap


@pytest.mark.parametrize("status", [0, 502, 504])
def test_maybe_started_counts_against_cap_and_goes_to_owner(tmp_path: Path, status: int) -> None:
    c, gh, hub = cfg(tmp_path, cap=1), FakeGitHub([issue(1), issue(2)]), FakeHub(post=relay.HubError(status))
    counts = run(c, gh, hub)
    assert (counts["needs_owner"], counts["skipped"]) == (1, 1) and hub.calls == ["POST"]
    run(c, gh, hub)
    assert hub.calls == ["POST"]


def test_interrupted_start_or_failed_write_never_restarts(tmp_path: Path) -> None:
    c, gh, hub = cfg(tmp_path), FakeGitHub([issue()]), FakeHub()
    relay.save_state(c.state_path, {"processed": {"42": {"status": "starting"}}, "starts": {}})
    assert run(c, gh, hub)["needs_owner"] == 1 and ("label", 42, relay.NEEDS_OWNER) in gh.writes and hub.calls == []
    c, hub = cfg(tmp_path / "write"), FakeHub(status="running")  # a failed GitHub write must not re-start
    assert run(c, FakeGitHub([issue()], fail_comment=True), hub)["errors"] == 1
    run(c, FakeGitHub([issue()]), hub)
    assert hub.calls.count("POST") == 1


def test_bad_request_rejected_once_and_failure_is_isolated(tmp_path: Path) -> None:
    c, gh = cfg(tmp_path), FakeGitHub([issue(1, explode=True), issue(2, body="no block"), issue(3)])
    counts = run(c, gh, FakeHub())
    assert (counts["errors"], counts["rejected"], counts["started"]) == (1, 1, 1)
    assert run(c, gh, FakeHub())["rejected"] == 0
