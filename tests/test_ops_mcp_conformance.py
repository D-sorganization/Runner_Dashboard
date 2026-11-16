"""Fleet Agent API Standard v1 ops-MCP conformance subset (#4305)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("mcp")

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO_ROOT = Path(__file__).resolve().parent.parent
for sub in ("backend", "clients/fleet"):
    path = str(REPO_ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)

from ops_mcp_contract import (  # noqa: E402
    CONTRACT_VERSION,
    MAX_TOOLS_LIST_ITEMS,
    MCP_JSON_SERVER_KEYS,
    NA_CONFORMANCE_CHECKS,
    OPS_MCP_STDIO_SERVERS,
    STRUCTURED_ERROR_KEYS,
    stamp_tool_payload,
)
from vuln_scan.sandbox import ExecResult, Sandbox  # noqa: E402
from vuln_scan.scan_mcp import ScanSandboxServer, build_server  # noqa: E402

from scripts import rd_threads_mcp  # noqa: E402

MCP_JSON = REPO_ROOT / ".mcp.json"
SCAN_CONTAINER = "vs-abcd1234ef567890"

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "0"}},
}


class _FakeDocker:
    def __call__(self, argv, timeout, cap, env) -> ExecResult:
        return ExecResult(0, "ok", "")


def _rpc_session(
    server_script: Path,
    messages: list[dict[str, Any]],
    *,
    env: dict[str, str] | None = None,
) -> list[dict]:
    merged = {**os.environ, "FLEET_API_URL": "http://127.0.0.1:9", "FLEET_API_TOKEN": "conformance", **(env or {})}
    stdin = "".join(json.dumps(m) + "\n" for m in messages)
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(server_script)],
        input=stdin,
        capture_output=True,
        text=True,
        env=merged,
        timeout=120,
        check=True,
    )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def _tool_payload(result: dict[str, Any]) -> dict[str, Any]:
    body = result["result"]["content"][0]["text"]
    return json.loads(body)


_SUBPROCESS_SERVERS = tuple(s for s in OPS_MCP_STDIO_SERVERS if s["id"] != "scan_sandbox")


@pytest.mark.parametrize("server", _SUBPROCESS_SERVERS, ids=[s["id"] for s in _SUBPROCESS_SERVERS])
def test_stdio_handshake_and_bounded_tool_list(server: dict[str, str]) -> None:
    script = REPO_ROOT / server["module_relpath"]
    assert script.is_file(), script
    responses = _rpc_session(script, [INIT, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    init = responses[0]["result"]
    assert init["protocolVersion"] == "2025-06-18"
    tools = responses[1]["result"]["tools"]
    assert 0 < len(tools) <= MAX_TOOLS_LIST_ITEMS
    for tool in tools:
        assert tool.get("name") and tool.get("inputSchema")


def test_fleet_invalid_tool_returns_structured_tool_error() -> None:
    from fleet_mcp import FleetMCPServer

    server = FleetMCPServer(client=MagicMock())
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "fleet_claim_issue", "arguments": {"repo": "Runner_Dashboard", "issue": 1, "bogus": 1}},
        }
    )
    assert response is not None
    payload = _tool_payload(response)
    assert payload["contract_version"] == CONTRACT_VERSION
    assert STRUCTURED_ERROR_KEYS <= set(payload.keys())


def test_rd_threads_failure_payload_is_structured() -> None:
    cli = MagicMock()
    cli.inbox.return_value = {"ok": False, "error": "missing_scope", "required_scope": "fleet.read"}
    result = rd_threads_mcp.RdThreadsMCP(cli).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "inbox", "arguments": {}}}
    )
    payload = _tool_payload(result)
    assert payload["contract_version"] == CONTRACT_VERSION
    assert STRUCTURED_ERROR_KEYS <= set(payload.keys())


def test_scan_sandbox_stdio_handshake_in_process(tmp_path: Path) -> None:
    server = build_server(
        ["--container", SCAN_CONTAINER, "--findings-out", str(tmp_path / "f.jsonl"), "--max-findings", "3"],
        runner=_FakeDocker(),
    )
    init = server.handle({**INIT, "id": 0})
    assert init is not None
    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed is not None
    tools = listed["result"]["tools"]
    assert 0 < len(tools) <= MAX_TOOLS_LIST_ITEMS


def test_scan_sandbox_tool_error_is_structured(tmp_path: Path) -> None:
    sandbox = Sandbox(SCAN_CONTAINER, _FakeDocker(), env={})
    server = ScanSandboxServer(sandbox, tmp_path / "f.jsonl", 2)
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "sandbox_exec", "arguments": {"argv": "rm -rf /"}},
    }
    response = server.handle(msg)
    assert response is not None
    payload = _tool_payload(response)
    assert STRUCTURED_ERROR_KEYS <= set(payload.keys())
    assert payload["contract_version"] == CONTRACT_VERSION


def test_mcp_json_registers_ops_servers_and_paths_exist() -> None:
    doc = json.loads(MCP_JSON.read_text(encoding="utf-8"))
    servers = doc.get("mcpServers") or {}
    assert MCP_JSON_SERVER_KEYS <= set(servers)
    for key in MCP_JSON_SERVER_KEYS:
        args = servers[key].get("args") or []
        assert args, f"{key} missing args"
        rel = args[-1].replace("${workspaceFolder}/", "")
        assert (REPO_ROOT / rel).is_file(), rel


def test_adoption_doc_lists_run_contract_na() -> None:
    text = (REPO_ROOT / "docs/agents/agent-api-adoption.md").read_text(encoding="utf-8")
    assert "run-contract" in text.lower()
    assert "N/A" in text
    for check in NA_CONFORMANCE_CHECKS:
        assert check.replace("_", "-") in text or check in text


@pytest.mark.asyncio
async def test_real_mcp_sdk_stdio_handshake_fleet() -> None:
    script = REPO_ROOT / "clients/fleet/fleet_mcp.py"
    env = {
        **os.environ,
        "FLEET_API_URL": "http://127.0.0.1:9",
        "FLEET_API_TOKEN": "sdk-smoke",
        "FLEET_AGENT": "conformance",
    }
    params = StdioServerParameters(command=sys.executable, args=[str(script)], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert 0 < len(tools.tools) <= MAX_TOOLS_LIST_ITEMS


def test_stamp_tool_payload_idempotent() -> None:
    first = stamp_tool_payload({"ok": True, "value": 1}, is_error=False)
    second = stamp_tool_payload(first, is_error=False)
    assert first == second
    assert first["contract_version"] == CONTRACT_VERSION
