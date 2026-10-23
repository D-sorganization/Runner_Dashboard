"""scan_sandbox MCP server: protocol, bound container, capped report_finding (#2572)."""

from __future__ import annotations

import io
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from vuln_scan.sandbox import ExecResult, Sandbox
from vuln_scan.scan_mcp import TOOL_NAMES, ScanSandboxServer, build_server

CONTAINER = "vs-abcd1234ef567890"
FINDING = {
    "title": "Command injection in export",
    "cwe": "CWE-78",
    "severity": "high",
    "confidence": "medium",
    "path": "src/export.py",
    "symbol": "export",
    "root_cause": "shell=True with user input",
}


class FakeDocker:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str], timeout: float, cap: int, env: Mapping[str, str]) -> ExecResult:
        self.calls.append(list(argv))
        return ExecResult(0, "hello", "")


def _server(tmp_path: Path, cap: int = 2) -> tuple[ScanSandboxServer, FakeDocker]:
    docker = FakeDocker()
    sandbox = Sandbox(CONTAINER, docker, env={})
    return ScanSandboxServer(sandbox, tmp_path / "findings.jsonl", cap), docker


def _call(server: ScanSandboxServer, name: str, arguments: object) -> dict:
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    response = server.handle(msg)
    assert response is not None
    return response


def test_initialize_and_tool_list(tmp_path: Path) -> None:
    server, _ = _server(tmp_path)
    init = server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
    assert init is not None and init["result"]["serverInfo"]["name"] == "scan_sandbox"
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed is not None
    assert tuple(t["name"] for t in listed["result"]["tools"]) == TOOL_NAMES
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_exec_runs_in_the_bound_container(tmp_path: Path) -> None:
    server, docker = _server(tmp_path)
    body = _call(server, "sandbox_exec", {"argv": ["id"], "container": "runner-1"})
    assert body["result"]["isError"] is False
    assert CONTAINER in docker.calls[0] and "runner-1" not in docker.calls[0]


def test_bad_requests_are_tool_errors_not_crashes(tmp_path: Path) -> None:
    server, docker = _server(tmp_path)
    assert _call(server, "sandbox_exec", {"argv": "rm -rf /"})["result"]["isError"]
    assert _call(server, "sandbox_exec", {"argv": ["ls"], "timeout_s": "9"})["result"]["isError"]
    assert _call(server, "sandbox_read", {"path": "/x", "max_bytes": True})["result"]["isError"]
    assert _call(server, "Bash", {"command": "id"})["error"]["code"] == -32602
    assert _call(server, "sandbox_list", ["/"])["error"]["code"] == -32602
    assert docker.calls == []


def test_report_finding_validates_writes_and_caps(tmp_path: Path) -> None:
    server, _ = _server(tmp_path, cap=2)
    bad = _call(server, "report_finding", {**FINDING, "cwe": "78"})
    assert bad["result"]["isError"]
    ok = json.loads(_call(server, "report_finding", FINDING)["result"]["content"][0]["text"])
    assert ok["accepted"] and ok["remaining"] == 1
    _call(server, "report_finding", {**FINDING, "symbol": "other"})
    capped = _call(server, "report_finding", {**FINDING, "symbol": "third"})
    assert capped["result"]["isError"] and "cap" in capped["result"]["content"][0]["text"]
    lines = (tmp_path / "findings.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_serve_loop_survives_garbage(tmp_path: Path) -> None:
    server, _ = _server(tmp_path)
    stdin = io.StringIO('not json\n\n{"jsonrpc": "2.0", "id": 7, "method": "ping"}\n[1]\n')
    stdout = io.StringIO()
    server.serve(stdin, stdout)
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert replies[0]["error"]["code"] == -32700
    assert replies[1] == {"jsonrpc": "2.0", "id": 7, "result": {}}
    assert replies[2]["error"]["code"] == -32600


def test_build_server_from_argv(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "should-not-pass")
    server = build_server(
        ["--container", CONTAINER, "--findings-out", str(tmp_path / "f.jsonl"), "--max-findings", "500"],
        runner=FakeDocker(),
    )
    assert server.max_findings == 100
    assert "GH_TOKEN" not in server.sandbox._env
