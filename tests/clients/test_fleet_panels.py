"""Expert-panel client tools: FleetClient, fleetctl and MCP, against the fake hub."""

from __future__ import annotations

import json
from typing import Any

import fleetctl
import pytest
from fleet_client import FleetAPIError, FleetArgumentError, FleetClient
from fleet_fixtures import _clean_fleet_env, fake_api  # noqa: F401 - pytest fixtures
from fleet_mcp import FleetMCPServer
from fleet_tools import BY_CLI, BY_TOOL

BASE = "/api/v1/staff/panels"
REQUEST = {
    "topic": "Should the relay poll or use webhooks?",
    "experts": [
        {"name": "Architect", "perspective": "Interfaces."},
        {"name": "Skeptic", "perspective": "Failure modes."},
        {"name": "Operator", "perspective": "Running it."},
    ],
}


def _client(fake_api: Any) -> FleetClient:
    return FleetClient(fake_api.url, token="tok", agent="codex")


def test_presets_and_list_are_gets(fake_api: Any) -> None:
    client = _client(fake_api)
    client.panel_presets()
    assert (fake_api.last.method, fake_api.last.path) == ("GET", f"{BASE}/presets")
    client.panel_list(limit=5)
    assert (fake_api.last.method, fake_api.last.path) == ("GET", BASE)
    assert fake_api.last.query["limit"] == "5"


def test_list_rejects_out_of_range_limit(fake_api: Any) -> None:
    with pytest.raises(FleetArgumentError):
        _client(fake_api).panel_list(limit=0)
    assert fake_api.requests == []


def test_start_posts_request_without_cost_confirmation(fake_api: Any) -> None:
    _client(fake_api).panel_start(REQUEST)
    assert (fake_api.last.method, fake_api.last.path) == ("POST", BASE)
    assert fake_api.last.body == REQUEST


def test_start_confirms_cost_only_when_passed_separately(fake_api: Any) -> None:
    _client(fake_api).panel_start(REQUEST, confirm_cost=True)
    assert fake_api.last.body["confirm_cost"] is True


@pytest.mark.parametrize(
    "bad",
    [
        "not an object",
        {"experts": REQUEST["experts"]},
        {"topic": "  ", "experts": REQUEST["experts"]},
        {"topic": "t"},
        {**REQUEST, "confirm_cost": True},
    ],
)
def test_start_rejects_bad_request_and_sends_nothing(fake_api: Any, bad: Any) -> None:
    with pytest.raises(FleetArgumentError):
        _client(fake_api).panel_start(bad)
    assert fake_api.requests == []


def test_start_over_threshold_surfaces_hub_400(fake_api: Any) -> None:
    fake_api.respond("POST", BASE, 400, {"detail": {"message": "over threshold"}})
    with pytest.raises(FleetAPIError) as info:
        _client(fake_api).panel_start(REQUEST)
    assert info.value.status == 400


def test_status_and_digest_paths(fake_api: Any) -> None:
    client = _client(fake_api)
    client.panel_status("th_abc123")
    assert fake_api.last.path == f"{BASE}/th_abc123"
    client.panel_digest("th_abc123")
    assert fake_api.last.path == f"{BASE}/th_abc123/digest"


@pytest.mark.parametrize("bad", ["../x", "th/../presets", "", "a b"])
def test_thread_id_is_validated(fake_api: Any, bad: str) -> None:
    with pytest.raises(FleetArgumentError):
        _client(fake_api).panel_digest(bad)
    assert fake_api.requests == []


def test_start_is_cli_only_and_reads_are_untrusted_tools() -> None:
    assert BY_CLI["panel-start"].tool is None
    assert {"fleet_panel_presets", "fleet_panel_list", "fleet_panel_status", "fleet_panel_digest"} <= set(BY_TOOL)
    assert BY_TOOL["fleet_panel_digest"].untrusted and BY_TOOL["fleet_panel_status"].untrusted


def test_mcp_digest_is_framed_as_untrusted(fake_api: Any) -> None:
    server = FleetMCPServer(_client(fake_api))
    out = server.call_tool({"name": "fleet_panel_digest", "arguments": {"thread_id": "th_1"}})
    assert out["isError"] is False
    assert "untrusted" in str(out["content"]).lower()


def test_fleetctl_panel_subcommands(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    def run(*argv: str) -> int:
        code = fleetctl.main(["--url", fake_api.url, "--as-agent", "codex", *argv])
        capsys.readouterr()
        return code

    assert run("panel-start", "--request", json.dumps(REQUEST)) == 0
    assert fake_api.last.body == REQUEST
    assert run("panel-start", "--request", json.dumps(REQUEST), "--confirm-cost") == 0
    assert fake_api.last.body["confirm_cost"] is True
    assert run("panel-digest", "th_9") == 0
    assert fake_api.last.path == f"{BASE}/th_9/digest"
    n = len(fake_api.requests)
    assert run("panel-start", "--request", json.dumps({"topic": "t"})) == 2
    assert len(fake_api.requests) == n
