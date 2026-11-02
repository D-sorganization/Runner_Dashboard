"""Barbara panel relay (#3711)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
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


def test_run_once_disabled_by_default(tmp_path: Path) -> None:
    cfg = relay.load_config({"BARB_PANEL_RELAY_ENABLED": "0", "FLEET_API_TOKEN": "t"})
    cfg = relay.RelayConfig(
        enabled=False,
        hub_url=cfg.hub_url,
        token="t",
        allowed_authors=frozenset(),
        daily_cap=3,
        state_path=tmp_path / "state.json",
    )
    assert relay.run_once(cfg, []) == [{"status": "disabled"}]


def test_process_issue_starts_panel_and_dedupes(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def fake_hub(method: str, url: str, token: str, body: dict | None) -> dict:
        calls.append((method, url))
        if method == "POST":
            return {"thread": {"id": "th_test"}}
        return {"status": "complete", "synthesis": "yes"}

    cfg = relay.RelayConfig(
        enabled=True,
        hub_url="http://hub.test",
        token="tok",
        allowed_authors=frozenset({"agent-codex"}),
        daily_cap=2,
        state_path=tmp_path / "state.json",
    )
    issue = {"number": 42, "user": {"login": "agent-codex"}, "body": "```panel-request\n" + json.dumps(SAMPLE) + "\n```"}
    state: dict = {"processed": {}, "count": 0}
    first = relay.process_issue(issue, cfg=cfg, state=state, hub=fake_hub)
    assert first["status"] == "started" and first["thread"] == "th_test"
    second = relay.process_issue(issue, cfg=cfg, state=state, hub=fake_hub)
    assert second["status"] == "deduped"
    assert calls[0][0] == "POST" and "/digest" in calls[1][1]
