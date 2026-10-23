"""Sanitized launch profile for the scanning agent (#2572, panel verdict q4 items 2-3).

A security scan is not a normal staff run. The claude CLI runs on the dashboard
host under its subscription login, with:

- an empty sterile cwd, so there is no repository checkout on the host
- no built-in tools (``--tools ""``, ``--restricted``)
- no MCP servers except ``scan_sandbox`` (``--strict-mcp-config``)
- no user or project settings (``--setting-sources ""``)
- ``dontAsk`` permissions, so only the allow-listed MCP tools run
- an environment reduced to an allowlist, with no GitHub or fleet tokens minted
- a pinned CLI version

Startup is fail-closed. :func:`check_init` refuses the run unless the
stream-json ``system/init`` event lists exactly the ``scan_sandbox`` tools and
server. :func:`check_event` then kills the run on any tool call outside that set.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from security import safe_subprocess_env
from vuln_scan.scan_mcp import SERVER_NAME, TOOL_NAMES

MCP_PREFIX = f"mcp__{SERVER_NAME}__"
ALLOWED_TOOLS = frozenset(MCP_PREFIX + name for name in TOOL_NAMES)
# Enough for the CLI to find its subscription login and run; nothing that grants
# repository, fleet or cloud access. Keys are compared case-insensitively (Windows).
ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "XDG_CONFIG_HOME",
        "CLAUDE_CONFIG_DIR",
    }
)
_FIXED_ENV = {
    "DISABLE_AUTOUPDATER": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_TELEMETRY": "1",
}


class LaunchRefused(RuntimeError):
    """The CLI did not come up in the sanitized profile; the run must abort."""


def scan_env(
    exec_timeout_s: int,
    base: Mapping[str, str] | None = None,
    *,
    claude_config_dir: str | None = None,
) -> dict[str, str]:
    """Allowlisted CLI environment, built on ``security.safe_subprocess_env``."""
    source = safe_subprocess_env() if base is None else dict(base)
    env = {k: v for k, v in source.items() if k.upper() in ENV_ALLOWLIST}
    env.update(_FIXED_ENV)
    if claude_config_dir:
        env["CLAUDE_CONFIG_DIR"] = claude_config_dir  # the staff seat's login
    # The CLI's per-tool MCP timeout must outlast the longest sandbox_exec, so a
    # long build or test inside the container is not cut off by the client.
    env["MCP_TOOL_TIMEOUT"] = str((exec_timeout_s + 60) * 1000)
    return env


def mcp_config(
    *,
    python: str,
    server_path: Path,
    container: str,
    findings_out: Path,
    max_findings: int,
    exec_timeout_s: int,
    docker_env: Mapping[str, str],
) -> dict[str, Any]:
    """The ``--mcp-config`` document: one stdio server bound to one container."""
    return {
        "mcpServers": {
            SERVER_NAME: {
                "type": "stdio",
                "command": python,
                "args": [
                    "-I",
                    str(server_path),
                    "--container",
                    container,
                    "--findings-out",
                    str(findings_out),
                    "--max-findings",
                    str(max_findings),
                    "--exec-timeout",
                    str(exec_timeout_s),
                ],
                "env": dict(docker_env),
            }
        }
    }


def cli_argv(cli: str, *, mcp_config_path: Path, model: str) -> list[str]:
    """argv for ``claude -p`` in the sanitized profile; the prompt goes on stdin."""
    return [
        cli,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--restricted",
        "--tools",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        str(mcp_config_path),
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        ",".join(sorted(ALLOWED_TOOLS)),
        "--model",
        model,
    ]


def write_mcp_config(directory: Path, config: Mapping[str, Any]) -> Path:
    path = directory / "scan_mcp.json"
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


def check_cli_version(version_output: str, pinned: str | None) -> str:
    """``claude --version`` must start with the operator's pinned version."""
    found = version_output.strip().split(" ", 1)[0]
    if not pinned:
        raise LaunchRefused("executor.cli_version is not pinned in the operator config")
    if found != pinned:
        raise LaunchRefused(f"claude CLI is {found or 'unknown'}, pinned {pinned}")
    return found


def check_init(event: Mapping[str, Any]) -> None:
    """Fail closed unless the session exposes exactly the scan_sandbox tools."""
    if event.get("type") != "system" or event.get("subtype") != "init":
        raise LaunchRefused("first stream event was not system/init")
    tools = event.get("tools")
    if not isinstance(tools, list) or set(tools) != ALLOWED_TOOLS:
        extra = (
            sorted(set(tools or []) - ALLOWED_TOOLS)
            if isinstance(tools, list)
            else tools
        )
        raise LaunchRefused(
            f"tool set is not exactly scan_sandbox (unexpected: {extra})"
        )
    servers = event.get("mcp_servers")
    if not isinstance(servers, list) or len(servers) != 1:
        raise LaunchRefused("expected exactly one MCP server")
    server = servers[0] if isinstance(servers[0], dict) else {}
    if server.get("name") != SERVER_NAME or server.get("status") != "connected":
        raise LaunchRefused(
            f"MCP server {server.get('name')!r} is not a connected scan_sandbox"
        )


def tool_calls(event: Mapping[str, Any]) -> list[str]:
    """Names of the tools an ``assistant`` stream event asks to run."""
    if event.get("type") != "assistant":
        return []
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    return [
        str(block.get("name"))
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]


def check_event(event: Mapping[str, Any]) -> None:
    """Kill switch: any tool call outside the scan_sandbox set aborts the run."""
    for name in tool_calls(event):
        if name not in ALLOWED_TOOLS:
            raise LaunchRefused(f"agent called {name!r}, outside the scan sandbox")
