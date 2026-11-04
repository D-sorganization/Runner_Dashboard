"""gh pr create drain-mode guard (#3915 slice 2)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from staff.gh_drain_gate_guard import (
    check_drain_gate_for_pr_create,
    fetch_hub_drain_status,
    parse_pr_create_args,
)

pytestmark = pytest.mark.unit


def test_parse_pr_create_args_extracts_repo_title_and_labels() -> None:
    argv = [
        "pr",
        "create",
        "-R",
        "D-sorganization/Gasification_Model",
        "--title",
        "feat: lane work",
        "--label",
        "agent:foo",
        "-l",
        "security",
    ]
    repo, title, labels = parse_pr_create_args(argv)
    assert repo == "D-sorganization/Gasification_Model"
    assert title == "feat: lane work"
    assert labels == ["agent:foo", "security"]


def test_fetch_hub_drain_status_uses_fleet_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"drained": True, "expires": None, "open_pr_count": 44, "threshold": 20}

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return FakeResponse()

    monkeypatch.setenv("HUB_URL", "http://hub.test:8321")
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-secret")
    with patch("staff.gh_drain_gate_guard.httpx.get", side_effect=fake_get):
        payload, ok = fetch_hub_drain_status("Gasification_Model", env={"HUB_URL": "http://hub.test:8321", "HUB_FLEET_TOKEN": "fleet-secret"})
    assert ok is True
    assert payload is not None and payload["drained"] is True
    assert captured["url"].startswith("http://hub.test:8321/api/directives/drain")
    assert "repo=D-sorganization%2FGasification_Model" in captured["url"] or "repo=Gasification_Model" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer fleet-secret"


def test_fetch_hub_drain_status_fail_open_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_URL", "http://hub.test:8321")
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-secret")

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise httpx.ConnectError("refused")

    with patch("staff.gh_drain_gate_guard.httpx.get", side_effect=boom):
        payload, ok = fetch_hub_drain_status("Gasification_Model", env={"HUB_URL": "http://hub.test:8321", "HUB_FLEET_TOKEN": "fleet-secret"})
    assert ok is False
    assert payload is None


def test_check_blocks_drained_non_exempt_pr() -> None:
    argv = ["pr", "create", "-R", "D-sorganization/Gasification_Model", "--title", "feat: spam"]
    with patch(
        "staff.gh_drain_gate_guard.fetch_hub_drain_status",
        return_value=({"drained": True, "open_pr_count": 44, "threshold": 20, "expires": "2026-11-05T07:59:00Z"}, True),
    ):
        allowed, err = check_drain_gate_for_pr_create(argv, env={"HUB_URL": "http://hub", "HUB_FLEET_TOKEN": "tok"})
    assert allowed is False
    assert "drain" in err.lower()
    assert "Gasification_Model" in err


def test_check_allows_exempt_security_pr_when_drained() -> None:
    argv = ["pr", "create", "-R", "Gasification_Model", "--title", "fix(security): rotate", "--label", "security"]
    with patch(
        "staff.gh_drain_gate_guard.fetch_hub_drain_status",
        return_value=({"drained": True, "open_pr_count": 44, "threshold": 20, "expires": None}, True),
    ):
        allowed, err = check_drain_gate_for_pr_create(argv, env={})
    assert allowed is True
    assert err == ""


def test_check_fail_open_when_hub_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["pr", "create", "-R", "Gasification_Model", "--title", "feat: lane"]
    with patch("staff.gh_drain_gate_guard.fetch_hub_drain_status", return_value=(None, False)):
        allowed, err = check_drain_gate_for_pr_create(argv, env={"HUB_URL": "http://hub", "HUB_FLEET_TOKEN": "tok"})
    assert allowed is True
    assert err == ""
    assert "fail-open" in capsys.readouterr().err.lower()


def test_check_skips_non_create_commands() -> None:
    allowed, err = check_drain_gate_for_pr_create(["pr", "list"], env={})
    assert allowed is True and err == ""
