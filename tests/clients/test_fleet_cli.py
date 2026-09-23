"""fleetctl CLI tests (#1228): JSON on stdout, exit codes, schema-derived arguments."""

from __future__ import annotations

import json
from typing import Any

import fleetctl
import pytest
from fleet_client import FleetClient
from fleet_fixtures import _clean_fleet_env, fake_api  # pytest fixtures
from fleet_tools import BY_CLI, BY_TOOL, COMMANDS


def _run(capsys: pytest.CaptureFixture[str], fake_api: Any, *argv: str) -> tuple[int, Any]:
    code = fleetctl.main(["--url", fake_api.url, *argv])
    return code, json.loads(capsys.readouterr().out)


def test_briefing(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, out = _run(capsys, fake_api, "briefing", "--repo", "Runner_Dashboard", "--agent", "grok")
    assert code == 0
    assert out["ok"] is True
    assert fake_api.last.path == "/api/coordination/briefing"
    assert fake_api.last.query == {"repo": "Runner_Dashboard", "agent": "grok"}
    assert fake_api.last.headers["x-requested-with"] == "XMLHttpRequest"


def test_dispatch_positional_role_and_dry_run(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, _ = _run(
        capsys, fake_api, "dispatch", "night-watch", "--repo", "Runner_Dashboard", "--issue", "4", "--dry-run"
    )
    assert code == 0
    assert fake_api.last.path == "/api/staff/night-watch/run"
    assert fake_api.last.body == {"repo": "Runner_Dashboard", "issue": 4, "machine": "auto", "dry_run": True}


def test_claim_uses_default_session(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, _ = _run(capsys, fake_api, "--as-session", "s-9", "claim", "--repo", "Runner_Dashboard", "--issue", "12")
    assert code == 0
    assert fake_api.last.body == {"repo": "Runner_Dashboard", "issue": 12, "session": "s-9", "intent": ""}


def test_register_presence_repeated_paths(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, _ = _run(
        capsys,
        fake_api,
        "register-presence",
        "--repo",
        "Runner_Dashboard",
        "--issue",
        "1228",
        "--branch",
        "feat/fleet-clients",
        "--session",
        "s-1",
        "--paths",
        "clients/",
        "--paths",
        "docs/agents/",
        "--goals",
        '{"ship": "clients"}',
    )
    assert code == 0
    assert fake_api.last.body["paths"] == ["clients/", "docs/agents/"]
    assert fake_api.last.body["goals"] == {"ship": "clients"}


def test_set_directives_json(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, _ = _run(capsys, fake_api, "set-directives", "--directives", '[{"text": "Ship #1192", "priority": 1}]')
    assert code == 0
    assert fake_api.last.method == "PUT"
    assert fake_api.last.body == {"directives": [{"text": "Ship #1192", "priority": 1, "repo": "*"}]}


def test_api_error_exit_1_with_error_json(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    fake_api.respond("POST", "/api/coordination/claims", 409, {"detail": "held by codex"})
    code, out = _run(capsys, fake_api, "claim", "--repo", "Runner_Dashboard", "--issue", "3", "--session", "s")
    assert code == 1
    assert out == {"error": "fleet_api_error", "status": 409, "body": {"detail": "held by codex"}}


def test_invalid_arguments_exit_2_nothing_sent(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    code, out = _run(capsys, fake_api, "dispatch", "night-watch", "--repo", "Runner_Dashboard")
    assert code == 2
    assert out["error"] == "invalid_arguments"
    assert fake_api.requests == []


def test_every_command_maps_to_a_client_method() -> None:
    for command in COMMANDS:
        assert callable(getattr(FleetClient, command.method)), command.method
        assert set(command.required) <= set(command.properties)
        assert set(command.positional) <= set(command.properties)
    assert len(BY_CLI) == len(COMMANDS)


def test_mcp_tool_set_is_the_contracted_list() -> None:
    assert set(BY_TOOL) == {
        "fleet_briefing",
        "fleet_priorities",
        "fleet_sessions",
        "fleet_inbox",
        "fleet_register_presence",
        "fleet_release_presence",
        "fleet_send_message",
        "fleet_check_claim",
        "fleet_claim_issue",
        "fleet_release_claim",
        "fleet_staff_summary",
        "fleet_staff_roster",
        "fleet_dispatch_role",
        "fleet_run_status",
        "fleet_directives",
    }
