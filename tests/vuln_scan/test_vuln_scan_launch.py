"""Sanitized CLI launch profile and fail-closed startup proof (#2572)."""

from __future__ import annotations

from pathlib import Path

import pytest
from vuln_scan import launch

# Shape observed from the real claude CLI (2.1.263) launched with cli_argv().
REAL_INIT = {
    "type": "system",
    "subtype": "init",
    "tools": [
        "mcp__scan_sandbox__report_finding",
        "mcp__scan_sandbox__sandbox_exec",
        "mcp__scan_sandbox__sandbox_list",
        "mcp__scan_sandbox__sandbox_read",
    ],
    "mcp_servers": [{"name": "scan_sandbox", "status": "connected"}],
    "permissionMode": "dontAsk",
}


def test_cli_argv_is_the_sanitized_profile(tmp_path: Path) -> None:
    argv = launch.cli_argv("claude", mcp_config_path=tmp_path / "m.json", model="claude-opus-5-5")
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    for flag in ("--strict-mcp-config", "--restricted", "--no-session-persistence", "--disable-slash-commands"):
        assert flag in argv
    allowed = argv[argv.index("--allowedTools") + 1].split(",")
    assert set(allowed) == launch.ALLOWED_TOOLS
    assert "bypassPermissions" not in argv and "--dangerously-skip-permissions" not in argv


def test_scan_env_is_an_allowlist_without_tokens() -> None:
    base = {
        "PATH": "/usr/bin",
        "HOME": "/home/scan",
        "GH_TOKEN": "x",
        "GITHUB_TOKEN": "x",
        "FLEET_API_TOKEN": "x",
        "ANTHROPIC_API_KEY": "x",
        "OPENAI_API_KEY": "x",
        "SSH_AUTH_SOCK": "/tmp/agent",
        "DOCKER_HOST": "ssh://scan@vm",
        "AWS_PROFILE": "prod",
    }
    env = launch.scan_env(600, base, claude_config_dir="/home/scan/.claude")
    assert set(env) == {
        "PATH",
        "HOME",
        "CLAUDE_CONFIG_DIR",
        "MCP_TOOL_TIMEOUT",
        "DISABLE_AUTOUPDATER",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
        "DISABLE_TELEMETRY",
    }
    assert env["MCP_TOOL_TIMEOUT"] == str(660 * 1000)


def test_mcp_config_binds_one_server_to_one_container(tmp_path: Path) -> None:
    cfg = launch.mcp_config(
        python="/usr/bin/python3",
        server_path=Path("/opt/rd/backend/vuln_scan/scan_mcp.py"),
        container="vs-abcd1234ef567890",
        findings_out=tmp_path / "f.jsonl",
        max_findings=30,
        exec_timeout_s=600,
        docker_env={"DOCKER_HOST": "ssh://scan@vm"},
    )
    assert list(cfg["mcpServers"]) == ["scan_sandbox"]
    server = cfg["mcpServers"]["scan_sandbox"]
    assert server["args"][0] == "-I" and "vs-abcd1234ef567890" in server["args"]
    assert server["env"] == {"DOCKER_HOST": "ssh://scan@vm"}


def test_real_init_event_passes() -> None:
    launch.check_init(REAL_INIT)


@pytest.mark.parametrize(
    "patch",
    [
        {"type": "assistant"},
        {"tools": [*REAL_INIT["tools"], "Bash"]},
        {"tools": [*REAL_INIT["tools"], "Read"]},
        {"tools": REAL_INIT["tools"][:2]},
        {"tools": None},
        {"mcp_servers": [{"name": "scan_sandbox", "status": "failed"}]},
        {"mcp_servers": [{"name": "scan_sandbox", "status": "connected"}, {"name": "fleet", "status": "connected"}]},
        {"mcp_servers": [{"name": "fleet", "status": "connected"}]},
    ],
)
def test_init_fails_closed(patch: dict) -> None:
    with pytest.raises(launch.LaunchRefused):
        launch.check_init({**REAL_INIT, **patch})


def _assistant(*names: str) -> dict:
    blocks = [{"type": "tool_use", "name": n, "input": {}} for n in names]
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}, *blocks]}}


def test_event_kill_switch() -> None:
    launch.check_event(_assistant("mcp__scan_sandbox__sandbox_exec"))
    launch.check_event({"type": "user", "message": {"content": "tool result"}})
    for name in ("Bash", "WebFetch", "Read", "mcp__fleet__fleet_briefing", "mcp__scan_sandbox__other"):
        with pytest.raises(launch.LaunchRefused):
            launch.check_event(_assistant("mcp__scan_sandbox__sandbox_read", name))


def test_cli_version_must_match_the_pin() -> None:
    assert launch.check_cli_version("2.1.263 (Claude Code)\n", "2.1.263") == "2.1.263"
    with pytest.raises(launch.LaunchRefused, match="pinned"):
        launch.check_cli_version("2.1.264 (Claude Code)", "2.1.263")
    with pytest.raises(launch.LaunchRefused, match="not pinned"):
        launch.check_cli_version("2.1.263 (Claude Code)", None)
