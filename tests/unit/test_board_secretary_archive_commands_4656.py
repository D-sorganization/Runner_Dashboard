"""Board-secretary may run only its archive validator, not a general shell (#4656)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from staff.adapter_policies import board_secretary_archive_command_allowed, board_secretary_launch_kwargs
from staff.adapters import ADAPTERS
from staff.board_secretary_persona import board_secretary_role_data
from staff.cloud_dispatch import resolve_execute_launch, resolve_plan_execution

_ARCHIVE_COMMANDS = (
    "python -m scripts.board.init_meeting_packet --date 2026-11-24",
    "python3 -m scripts.board.apply_proposal_decisions --date 2026-11-24",
    "python -m scripts.board.validate_library_inventory",
    "pytest tests/test_board_meeting_init.py -q",
    "pytest tests/test_library_inventory.py",
    "pytest scripts/board/validate_library_inventory.py",
)

_ARBITRARY_SHELL = (
    "bash -lc 'python -m scripts.board.init_meeting_packet'",
    "python -c 'import os; os.system(\"rm -rf /\")'",
    "python -m scripts.other",
    "pytest",
    "pytest tests/test_staff_roles.py",
    "pytest tests/test_board_meeting_init.py -p evil_plugin",
    "pytest tests/test_board_meeting_init.py -p=evil_plugin",
    "pytest tests/test_board_meeting_init.py --override-ini x=y",
    "rm -rf docs/board-meetings",
    "sudo python -m scripts.board.init_meeting_packet",
    "python -m scripts.board.init_meeting_packet && echo pwned",
    "python -m scripts.board.init_meeting_packet & touch /tmp/board-secretary-escape",
)


@pytest.mark.unit
@pytest.mark.parametrize("command", _ARCHIVE_COMMANDS)
def test_archive_validation_commands_are_allowed(command: str) -> None:
    assert board_secretary_archive_command_allowed(command) is True


@pytest.mark.unit
@pytest.mark.parametrize("command", _ARBITRARY_SHELL)
def test_arbitrary_shell_stays_denied(command: str) -> None:
    assert board_secretary_archive_command_allowed(command) is False


@pytest.mark.unit
def test_role_keeps_host_shell_false() -> None:
    permissions = board_secretary_role_data()["permissions"]
    assert permissions["host_shell"] is False
    assert permissions["merge"] is False


@pytest.mark.unit
def test_claude_unattended_argv_scopes_board_secretary_shell(tmp_path: Path) -> None:
    argv = ADAPTERS["claude"].build_command(
        "archive the meeting", str(tmp_path), model=None, role="board-secretary"
    )
    allowed = set(argv[argv.index("--allowedTools") + 1].split(","))
    assert "Bash" not in allowed
    assert "Bash(python -m scripts.board:*)" in allowed
    assert "Bash(python3 -m scripts.board:*)" in allowed
    assert "Bash(python:*)" not in allowed
    assert "Bash(python3:*)" not in allowed
    assert "Bash(pytest:*)" not in allowed
    assert "Bash(pytest tests/test_board_meeting_init.py:*)" not in allowed
    assert "Bash(pytest tests/test_board_meeting_init.py)" in allowed
    assert "Bash(pytest tests/test_board_meeting_init.py -q)" in allowed
    assert "Bash(git:*)" in allowed
    assert "--settings" in argv


@pytest.mark.unit
def test_other_roles_keep_the_shared_python_allow_list() -> None:
    argv = ADAPTERS["claude"].build_command("do it", "/tmp/wt", model=None)
    allowed = set(argv[argv.index("--allowedTools") + 1].split(","))
    assert "Bash(python:*)" in allowed
    assert "Bash(pytest:*)" in allowed


@pytest.mark.unit
def test_launch_kwargs_are_scoped_to_board_secretary() -> None:
    assert board_secretary_launch_kwargs("board-secretary", {}) == {"role": "board-secretary"}
    assert board_secretary_launch_kwargs("night-watch", {"read_only": True}) == {"read_only": True}


@pytest.mark.unit
def test_plan_defers_settings_until_the_real_workdir_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Planning must not mkdir the ``<workdir>`` placeholder (#4656 review)."""
    monkeypatch.chdir(tmp_path)
    req = SimpleNamespace(execution_mode="local", issue=None, repo="Runner_Dashboard", pr=None)
    _mode, planned, _stdin, _lease = resolve_plan_execution(
        req,
        "board-secretary",
        "claude",
        ADAPTERS["claude"],
        "archive the meeting",
        None,
        True,
        {},
    )
    assert not (tmp_path / "<workdir>").exists()
    assert "--settings" in planned

    real = tmp_path / "run"
    real.mkdir()

    class _Plan:
        execution_mode = "local"
        model = None
        role = "board-secretary"
        provider = "claude"
        argv = planned
        cloud_stdin = ""

    launch_argv, _label, stdin_payload = resolve_execute_launch(
        _Plan(), ADAPTERS["claude"], "archive the meeting", real, {}, {}
    )
    assert stdin_payload is None
    settings = Path(launch_argv[launch_argv.index("--settings") + 1])
    assert settings.is_file()
    assert settings.is_relative_to(real)
    hook = json.loads(settings.read_text(encoding="utf-8"))
    assert "staff.board_secretary_bash_hook" in hook["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert not (tmp_path / "<workdir>").exists()


@pytest.mark.unit
def test_execute_launch_uses_the_scoped_claude_allow_list(tmp_path) -> None:
    class _Plan:
        execution_mode = "local"
        model = None
        role = "board-secretary"
        provider = "claude"
        argv: list[str] = []
        cloud_stdin = ""

    argv, _label, stdin_payload = resolve_execute_launch(
        _Plan(), ADAPTERS["claude"], "archive the meeting", tmp_path, {}, {}
    )
    assert stdin_payload is None
    allowed = set(argv[argv.index("--allowedTools") + 1].split(","))
    assert "Bash(python -m scripts.board:*)" in allowed
    assert "Bash(python:*)" not in allowed
    assert "--settings" in argv


def _hook_code(command: str) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    backend = Path(__file__).resolve().parents[2] / "backend"
    proc = subprocess.run(
        [sys.executable, "-m", "staff.board_secretary_bash_hook"],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(backend)},
    )
    return proc.returncode


@pytest.mark.unit
def test_launch_hook_rejects_plugin_injection_and_shell_syntax(tmp_path) -> None:
    """The settings file named on the Claude argv is the execution boundary."""
    argv = ADAPTERS["claude"].build_command(
        "archive the meeting", str(tmp_path), model=None, role="board-secretary"
    )
    settings = Path(argv[argv.index("--settings") + 1])
    hook = json.loads(settings.read_text(encoding="utf-8"))
    command = hook["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "staff.board_secretary_bash_hook" in command
    assert _hook_code("pytest tests/test_board_meeting_init.py -p evil_plugin") == 2
    assert _hook_code("python -m scripts.board.init_meeting_packet --date 2026-11-24 && echo pwned") == 2
    assert _hook_code("python -m scripts.board.init_meeting_packet & touch /tmp/board-secretary-escape") == 2
    assert _hook_code("python -m scripts.board.init_meeting_packet&touch /tmp/board-secretary-escape") == 2
    assert _hook_code("git status & touch /tmp/board-secretary-escape") == 2
    assert _hook_code("pytest tests/test_board_meeting_init.py -q") == 0
    assert _hook_code("python -m scripts.board.init_meeting_packet --date 2026-11-24") == 0
    assert _hook_code("git status") == 0
    assert _hook_code("rm -rf docs/board-meetings") == 2
