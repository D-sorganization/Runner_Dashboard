"""Unit tests for barb_backlog_push_lib (#3711 extension, PR slice a)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import barb_backlog_push_lib as lib  # noqa: E402
import barb_panel_relay as panel  # noqa: E402

BOT = "d-sorgclaudeagent[bot]"
TODAY = "2026-11-06"
VALID = {"repos": ["Runner_Dashboard"], "duration_min": 90, "lanes": 2}
BODY = "```backlog-push-request\n" + json.dumps(VALID) + "\n```"


def test_parse_and_bounds() -> None:
    assert lib.parse_backlog_push_request(BODY)["duration_min"] == 90
    cfg = lib.BacklogPushConfig(True, "http://h", "t", frozenset(), 2, Path("/tmp/x"), "D/x")
    lib.validate_bounds({"duration_min": 30, "lanes": 1}, cfg)
    with pytest.raises(ValueError, match="duration_min"):
        lib.validate_bounds({"duration_min": 29, "lanes": 1}, cfg)
    with pytest.raises(ValueError, match="lanes"):
        lib.validate_bounds({"duration_min": 30, "lanes": 5}, cfg)


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
        lib.parse_backlog_push_request(body)


def test_disabled_by_default_via_load_config() -> None:
    cfg = lib.load_config({"RELAY_BACKLOG_PUSH_ENABLED": "0", "BARB_PANEL_RELAY_AUTHORS": BOT})
    assert not cfg.enabled
    assert cfg.daily_cap == 2


def test_push_daily_cap_reached() -> None:
    assert lib.push_daily_cap_reached({TODAY: 2}, TODAY, 2)
    assert not lib.push_daily_cap_reached({TODAY: 1}, TODAY, 2)
    assert not lib.push_daily_cap_reached({TODAY: 5}, "2026-11-07", 2)


def test_single_flight_active() -> None:
    assert lib.single_flight_active({"issue": 1})
    assert not lib.single_flight_active(None)


def test_build_launch_plan_never_includes_issue_body() -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    sup, lanes = lib.session_names(1, 1, now)
    plan = lib.build_launch_plan(VALID, sup, lanes)
    joined = "\n".join(p["prompt"] for p in plan)
    assert "DROP TABLE" not in joined and "Runner_Dashboard" in joined
    assert "Sidekick packaging" in joined


def test_hub_resource_disk_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(_method: str, _url: str, _token: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"disk": {"pressure": {"status": "critical", "reasons": ["pool full"]}}}

    monkeypatch.setattr(panel, "_request", fake_get)
    assert "disk-pressure" in (lib.hub_resource_refusal("http://hub", "t") or "")
