"""Dispatch-exclusion client tools (#2544): FleetClient, fleetctl and MCP, against the fake hub."""

from __future__ import annotations

import json
from typing import Any

import fleetctl
import pytest
from fleet_client import FleetAPIError, FleetArgumentError, FleetClient
from fleet_fixtures import _clean_fleet_env, fake_api  # noqa: F401 - pytest fixtures
from fleet_mcp import FleetMCPServer
from fleet_tools import BY_CLI, BY_TOOL

BASE = "/api/v1/staff/exclusions"


def _client(fake_api: Any, **kw: Any) -> FleetClient:
    return FleetClient(fake_api.url, token="tok", agent="claude", **kw)


def test_list_sends_get_with_token(fake_api: Any) -> None:
    _client(fake_api).exclusions_list()
    assert (fake_api.last.method, fake_api.last.path) == ("GET", BASE)
    assert "tok" in json.dumps(fake_api.last.headers)


def test_add_posts_body_and_defaults_owner_to_agent(fake_api: Any) -> None:
    _client(fake_api).exclusion_add(repo="Tools", reason="user editing", issue=5, path_glob="src/**")
    assert (fake_api.last.method, fake_api.last.path) == ("POST", BASE)
    assert fake_api.last.body == {
        "repo": "Tools",
        "reason": "user editing",
        "owner": "claude",
        "issue": 5,
        "path_glob": "src/**",
    }


def test_add_requires_reason_and_sends_nothing(fake_api: Any) -> None:
    with pytest.raises(FleetArgumentError):
        _client(fake_api).exclusion_add(repo="Tools", reason="  ")
    assert fake_api.requests == []


def test_add_requires_owner_when_no_agent(fake_api: Any) -> None:
    with pytest.raises(FleetArgumentError, match="owner"):
        FleetClient(fake_api.url, token="t").exclusion_add(repo="Tools", reason="r")


def test_remove_deletes_by_id(fake_api: Any) -> None:
    _client(fake_api).exclusion_remove("excl-abc123")
    assert (fake_api.last.method, fake_api.last.path) == ("DELETE", f"{BASE}/excl-abc123")


def test_remove_rejects_bad_id(fake_api: Any) -> None:
    with pytest.raises(FleetArgumentError):
        _client(fake_api).exclusion_remove("../x")
    assert fake_api.requests == []


@pytest.mark.parametrize(
    ("call", "method", "scope"),
    [
        (lambda c: c.exclusions_list(), "GET", "staff.read"),
        (lambda c: c.exclusion_add(repo="T", reason="r"), "POST", "staff.holds.write"),
        (lambda c: c.exclusion_remove("excl-1"), "DELETE", "staff.holds.write"),
    ],
)
def test_403_names_missing_scope(fake_api: Any, call: Any, method: str, scope: str) -> None:
    fake_api.respond(method, BASE if method != "DELETE" else f"{BASE}/excl-1", 403, {"detail": "forbidden"})
    with pytest.raises(FleetAPIError) as info:
        call(_client(fake_api))
    assert info.value.status == 403
    assert scope in info.value.to_envelope()["message"]


def test_fleetctl_subcommands(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    def run(*argv: str) -> int:
        code = fleetctl.main(["--url", fake_api.url, "--as-agent", "claude", *argv])
        capsys.readouterr()
        return code

    assert run("exclusions", "list") == 0
    assert fake_api.last.method == "GET"
    assert run("exclusions", "add", "--repo", "Tools", "--reason", "busy", "--issue", "9") == 0
    assert fake_api.last.body["issue"] == 9 and fake_api.last.body["reason"] == "busy"
    assert run("exclusions", "remove", "excl-1") == 0
    assert fake_api.last.path == f"{BASE}/excl-1"
    n = len(fake_api.requests)
    assert run("exclusions", "add", "--repo", "Tools", "--reason", " ") == 2
    assert len(fake_api.requests) == n


def test_mcp_tools_registered_and_callable(fake_api: Any) -> None:
    assert {"fleet_exclusions_list", "fleet_exclusion_add", "fleet_exclusion_remove"} <= set(BY_TOOL)
    assert BY_TOOL["fleet_exclusion_add"].required == ("repo", "reason")
    assert BY_CLI["exclusions add"].tool == "fleet_exclusion_add"
    server = FleetMCPServer(_client(fake_api))
    out = server.call_tool({"name": "fleet_exclusion_add", "arguments": {"repo": "Tools", "reason": "r"}})
    assert out["isError"] is False
    missing = server.call_tool({"name": "fleet_exclusion_add", "arguments": {"repo": "Tools"}})
    assert missing["isError"] is True
