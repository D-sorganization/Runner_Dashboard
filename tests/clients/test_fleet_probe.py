"""TDD tests for Activity T4 manual-session shell shims and uncaptured-CLI probe (#2870)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fleet_client import FleetArgumentError
from fleet_fixtures import _clean_fleet_env, fake_api  # noqa: F401
from fleet_probe import (
    emit_recorder_heartbeat,
    install_shell_shims,
    probe_uncaptured_processes,
    render_shell_shims,
    uninstall_shell_shims,
)


@pytest.mark.parametrize("shell", ("bash", "zsh", "powershell"))
def test_shim_install_and_uninstall_are_idempotent_and_reversible(tmp_path: Path, shell: str) -> None:
    rc_file = tmp_path / f"profile.{shell}"
    original = '# Existing user config\nexport PATH="$HOME/bin:$PATH"\n'
    rc_file.write_text(original, encoding="utf-8")

    changed_first = install_shell_shims(rc_file, shell=shell)
    installed_text = rc_file.read_text(encoding="utf-8")
    assert changed_first is True
    for cli in ("claude", "codex", "agy", "cursor-agent"):
        assert cli in installed_text
    assert "FLEET_REC_ENABLED" in installed_text

    changed_second = install_shell_shims(rc_file, shell=shell)
    assert changed_second is False
    assert rc_file.read_text(encoding="utf-8") == installed_text

    removed_first = uninstall_shell_shims(rc_file)
    assert removed_first is True
    assert rc_file.read_text(encoding="utf-8") == original

    removed_second = uninstall_shell_shims(rc_file)
    assert removed_second is False
    assert rc_file.read_text(encoding="utf-8") == original


def test_probe_reports_uncaptured_processes_without_command_lines_or_arguments(
    fake_api: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLEET_NODE", "wsl-node-01")
    fake_api.respond(
        "GET",
        "/api/v1/activity/sessions",
        200,
        {
            "items": [
                {
                    "source": "recorder",
                    "session_id": "codex-wsl-node-01-20261029-111111",
                    "node": "wsl-node-01",
                    "status": "live",
                }
            ],
            "scan_limit": 4000,
            "truncated": False,
        },
    )
    fake_api.respond("POST", "/api/v1/activity/events", 200, [{"ok": True}])

    sensitive_arg = "ghp_" + "S" * 36
    raw_processes = [
        {
            "pid": 4101,
            "process_name": "/usr/local/bin/claude",
            "started_at": "2026-10-29T22:00:00Z",
            "cmdline": f"claude --flag-val {sensitive_arg} --print",
            "args": ["--flag-val", sensitive_arg],
        },
        {
            "pid": 4102,
            "process_name": "codex",
            "started_at": "2026-10-29T22:01:00Z",
            "session_id": "codex-wsl-node-01-20261029-111111",
        },
        {
            "pid": 4103,
            "process_name": "python3",
            "started_at": "2026-10-29T22:02:00Z",
        },
    ]

    result = probe_uncaptured_processes(url=fake_api.url, process_scanner=lambda: raw_processes)
    assert result["uncaptured_count"] == 1
    assert result["reported_sessions"] == ["claude-uncaptured-wsl-node-01-4101"]

    post_reqs = [req for req in fake_api.requests if req.method == "POST"]
    assert len(post_reqs) == 1
    events = post_reqs[0].body
    assert len(events) == 1
    ev = events[0]
    assert ev["source"] == "recorder"
    assert ev["kind"] == "uncaptured_process"
    assert ev["session_id"] == "claude-uncaptured-wsl-node-01-4101"
    assert ev["payload"] == {"process_name": "claude", "started_at": "2026-10-29T22:00:00Z"}
    raw_serialized = json.dumps(events)
    assert sensitive_arg not in raw_serialized
    for forbidden_key in ("cmdline", "args", "argv", "command"):
        assert forbidden_key not in ev["payload"]


def test_recorder_health_heartbeat_and_dbc_validation(fake_api: Any) -> None:
    fake_api.respond("POST", "/api/v1/activity/events", 200, [{"ok": True}])
    hb = emit_recorder_heartbeat(url=fake_api.url, node="wsl-node-01", adapter="pty", uncaptured_count=2)
    assert hb["ok"] is True
    sent = fake_api.requests[0].body[0]
    assert sent["kind"] == "recorder_heartbeat"
    assert sent["payload"] == {"adapter": "pty", "healthy": True, "uncaptured_count": 2}

    with pytest.raises(FleetArgumentError, match="unsupported shell"):
        render_shell_shims(shell="csh")
