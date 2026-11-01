"""Barbara panel relay pass (#3711): fake GitHub and hub, no network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fleet_validators import FleetAPIError

from scripts import barb_panel_relay as relay

TODAY = "2026-11-01"
REQUEST = {"topic": "Poll or webhooks?", "preset": "engineering-review"}
PRESETS = {
    "presets": [
        {
            "id": "engineering-review",
            "experts": [{"name": n, "perspective": "p", "provider": "claude", "available": True} for n in "ABC"],
        }
    ]
}


class FakeGitHub:
    def __init__(self, issues: list[dict[str, Any]]) -> None:
        self.issues, self.writes = issues, []

    def open_requests(self, repo: str) -> list[dict[str, Any]]:
        return self.issues

    def comment(self, repo: str, number: int, body: str) -> None:
        self.writes.append(("comment", number, body))

    def label(self, repo: str, number: int, name: str) -> None:
        self.writes.append(("label", number, name))

    def close(self, repo: str, number: int) -> None:
        self.writes.append(("close", number, ""))


class FakeHub:
    def __init__(self, post: Any = None, digest: Any = None) -> None:
        self.post = post if post is not None else {"thread": {"id": "th_1"}, "estimate": {}}
        self.digest = digest if digest is not None else {"status": "running"}
        self.calls: list[tuple[str, str, Any]] = []

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs.get("body")))
        result = PRESETS if path.endswith("/presets") else self.post if method == "POST" else self.digest
        if isinstance(result, Exception):
            raise result
        return result


def _issue(number: int = 7, login: str = "d-sorgcodexagent[bot]", data: Any = REQUEST, labels: tuple = ()) -> dict:
    body = f"```json panel-request\n{json.dumps(data)}\n```"
    return {"number": number, "user": {"login": login}, "body": body, "labels": [{"name": n} for n in labels]}


def _run(tmp_path: Path, issues: list[dict], hub: FakeHub, *, cap: int = 3, dry: bool = False) -> tuple:
    config = relay.RelayConfig("o/barbara", relay.req.parse_authors(None), cap, tmp_path / "s.json", dry)
    github = FakeGitHub(issues)
    state = relay.RelayState(config.state_path)
    counts = relay.PanelRelay(config, github, hub, state, TODAY).run()
    return counts, github, state


def test_off_unless_enabled() -> None:
    assert relay.config_from_env({}, dry_run=False) is None
    assert relay.config_from_env({"PANEL_RELAY_ENABLED": "1"}, dry_run=False) is not None
    assert relay.main([]) == 0  # kill switch off: a pass is a no-op


def test_codex_request_starts_panel_without_cost_confirmation(tmp_path: Path) -> None:
    hub = FakeHub()
    counts, github, state = _run(tmp_path, [_issue()], hub)
    posted = next(body for method, _, body in hub.calls if method == "POST")
    assert counts["started"] == 1 and "confirm_cost" not in posted and len(posted["experts"]) == 3
    assert ("label", 7, relay.STARTED) in github.writes
    assert json.loads((tmp_path / "s.json").read_text())["issues"]["7"]["thread_id"] == "th_1"


def test_unlisted_author_and_labelled_issues_are_ignored(tmp_path: Path) -> None:
    hub = FakeHub()
    issues = [_issue(1, login="stranger"), _issue(2, labels=(relay.REJECTED,))]
    counts, github, _ = _run(tmp_path, issues, hub)
    assert counts["skipped"] == 1 and github.writes == [] and hub.calls == []


def test_daily_cap_holds_later_requests(tmp_path: Path) -> None:
    counts, _, state = _run(tmp_path, [_issue(1), _issue(2)], FakeHub(), cap=1)
    assert counts["started"] == 1 and counts["skipped"] == 1 and state.starts[TODAY] == 1


def test_bad_block_is_rejected_on_the_issue(tmp_path: Path) -> None:
    counts, github, _ = _run(tmp_path, [_issue(data={"topic": "t", "preset": "x", "confirm_cost": True})], FakeHub())
    assert counts["rejected"] == 1 and ("label", 7, relay.REJECTED) in github.writes


def test_over_threshold_goes_to_owner(tmp_path: Path) -> None:
    over = FleetAPIError(400, {"detail": {"message": "too much", "estimate": {"exceeds_threshold": True}}})
    counts, github, _ = _run(tmp_path, [_issue()], FakeHub(post=over))
    assert counts["needs_owner"] == 1 and ("label", 7, relay.NEEDS_OWNER) in github.writes


def test_busy_hub_retries_next_pass_and_auth_failure_stops(tmp_path: Path) -> None:
    counts, github, state = _run(tmp_path, [_issue()], FakeHub(post=FleetAPIError(429, {})))
    assert counts["started"] == 0 and github.writes == [] and state.issues == {}
    with pytest.raises(FleetAPIError):
        _run(tmp_path, [_issue()], FakeHub(post=FleetAPIError(403, {})))


def test_finished_panel_posts_fenced_digest_and_closes(tmp_path: Path) -> None:
    _run(tmp_path, [_issue()], FakeHub())
    digest = {"thread_id": "th_1", "status": "complete", "consensus": "agree", "synthesis": "Do X. @someone ```"}
    counts, github, state = _run(tmp_path, [], FakeHub(digest=digest))
    comment = next(body for kind, _, body in github.writes if kind == "comment")
    assert counts["done"] == 1 and ("close", 7, "") in github.writes and state.issues["7"]["status"] == "done"
    assert "````text\nDo X. @someone ```\n````" in comment


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    hub = FakeHub()
    counts, github, _ = _run(tmp_path, [_issue(), _issue(8, data={"topic": "t"})], hub, dry=True)
    assert github.writes == [] and not (tmp_path / "s.json").exists()
    assert all(method == "GET" for method, _, _ in hub.calls) and counts["rejected"] == 1
