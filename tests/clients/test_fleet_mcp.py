"""fleet_mcp stdio server tests (#1228): real subprocess, JSON-RPC over stdin/stdout."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fleet_fixtures import _clean_fleet_env, fake_api  # pytest fixtures

SERVER = Path(__file__).resolve().parents[2] / "clients" / "fleet" / "fleet_mcp.py"


def _session(fake_api: Any, messages: list[Any], extra_env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Feed ``messages`` (dicts, or raw strings) to a fresh server; return the parsed stdout lines."""
    env = {**os.environ, "FLEET_API_URL": fake_api.url, "FLEET_API_TOKEN": "mcp-tok", **(extra_env or {})}
    stdin = "".join((m if isinstance(m, str) else json.dumps(m)) + "\n" for m in messages)
    proc = subprocess.run(  # noqa: S603 - fixed interpreter + repo script
        [sys.executable, str(SERVER)], input=stdin, capture_output=True, text=True, env=env, timeout=60, check=True
    )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "0"}},
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def _call(msg_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call", "params": {"name": name, "arguments": arguments}}


def test_handshake_list_and_call(fake_api: Any) -> None:
    responses = _session(
        fake_api,
        [
            INIT,
            INITIALIZED,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            _call(3, "fleet_briefing", {"repo": "Runner_Dashboard", "agent": "codex"}),
            {"jsonrpc": "2.0", "id": 4, "method": "ping"},
        ],
    )
    # The notification gets no response: exactly one line per request id.
    assert [r["id"] for r in responses] == [1, 2, 3, 4]
    init = responses[0]["result"]
    assert init["protocolVersion"] == "2025-06-18"
    assert init["serverInfo"]["name"] == "fleet"
    assert "tools" in init["capabilities"]

    tools = {t["name"]: t for t in responses[1]["result"]["tools"]}
    assert len(tools) == 16
    claim_schema = tools["fleet_claim_issue"]["inputSchema"]
    assert claim_schema["type"] == "object"
    assert set(claim_schema["required"]) == {"repo", "issue"}
    assert tools["fleet_dispatch_role"]["inputSchema"]["properties"]["dry_run"]["type"] == "boolean"
    assert set(tools["fleet_ack_message"]["inputSchema"]["required"]) == {"repo", "message_id"}
    assert "agent name" not in tools["fleet_send_message"]["inputSchema"]["properties"]["to"]["description"]

    call = responses[2]["result"]
    assert call["isError"] is False
    assert call["content"][0]["type"] == "text"
    payload = json.loads(call["content"][0]["text"])
    assert payload["echo"]["path"] == "/api/coordination/briefing"
    assert responses[3]["result"] == {}

    rec = fake_api.last
    assert rec.query == {"repo": "Runner_Dashboard", "agent": "codex"}
    assert rec.headers["authorization"] == "Bearer mcp-tok"
    assert rec.headers["x-requested-with"] == "XMLHttpRequest"


def test_write_tool_uses_env_identity(fake_api: Any) -> None:
    responses = _session(
        fake_api,
        [INIT, _call(2, "fleet_claim_issue", {"repo": "Runner_Dashboard", "issue": 9, "intent": "tests"})],
        {"FLEET_AGENT": "gemini", "FLEET_SESSION": "gemini-1"},
    )
    assert responses[1]["result"]["isError"] is False
    assert fake_api.last.body == {
        "repo": "Runner_Dashboard",
        "issue": 9,
        "agent": "gemini",
        "session": "gemini-1",
        "intent": "tests",
    }


def test_api_error_is_tool_error(fake_api: Any) -> None:
    fake_api.respond("POST", "/api/coordination/claims", 409, {"detail": {"held": True, "agent": "claude"}})
    responses = _session(
        fake_api, [INIT, _call(2, "fleet_claim_issue", {"repo": "Runner_Dashboard", "issue": 9, "session": "s"})]
    )
    result = responses[1]["result"]
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["status"] == 409


@pytest.mark.parametrize(
    "arguments",
    [{"repo": "Runner_Dashboard"}, {"repo": "Runner_Dashboard", "issue": 1, "bogus": 1}, {"repo": "../x", "issue": 1}],
)
def test_invalid_arguments_are_tool_errors_and_send_nothing(fake_api: Any, arguments: dict[str, Any]) -> None:
    responses = _session(fake_api, [INIT, _call(2, "fleet_check_claim", arguments)])
    result = responses[1]["result"]
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["error"] == "invalid_arguments"
    assert fake_api.requests == []


def test_protocol_errors(fake_api: Any) -> None:
    responses = _session(
        fake_api,
        [
            "{not json",
            _call(2, "fleet_nope", {}),
            {"jsonrpc": "2.0", "id": 3, "method": "resources/list"},
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 2}},
            {"jsonrpc": "2.0", "id": 5, "method": "ping"},
        ],
    )
    assert [r.get("id") for r in responses] == [None, 2, 3, 5]
    assert responses[0]["error"]["code"] == -32700
    assert responses[1]["error"]["code"] == -32602
    assert responses[2]["error"]["code"] == -32601
    assert responses[3]["result"] == {}


def test_unreachable_api_is_tool_error_not_crash(fake_api: Any) -> None:
    responses = _session(
        fake_api,
        [INIT, _call(2, "fleet_priorities", {}), {"jsonrpc": "2.0", "id": 3, "method": "ping"}],
        {"FLEET_API_URL": "http://127.0.0.1:9", "FLEET_API_TIMEOUT": "2"},
    )
    assert responses[1]["result"]["isError"] is True
    assert json.loads(responses[1]["result"]["content"][0]["text"])["status"] == 0
    assert responses[2]["result"] == {}
