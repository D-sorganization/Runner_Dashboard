"""Board-secretary may run only its archive validator, not a general shell (#4656)."""

from __future__ import annotations

import pytest
from staff.adapter_policies import board_secretary_archive_command_allowed
from staff.adapters import ADAPTERS
from staff.board_secretary_persona import board_secretary_role_data

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
    "rm -rf docs/board-meetings",
    "sudo python -m scripts.board.init_meeting_packet",
    "python -m scripts.board.init_meeting_packet && echo pwned",
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
def test_claude_unattended_argv_scopes_board_secretary_shell() -> None:
    argv = ADAPTERS["claude"].build_command("archive the meeting", "/tmp/wt", model=None, role="board-secretary")
    allowed = set(argv[argv.index("--allowedTools") + 1].split(","))
    assert "Bash" not in allowed
    assert "Bash(python -m scripts.board:*)" in allowed
    assert "Bash(python3 -m scripts.board:*)" in allowed
    assert "Bash(python:*)" not in allowed
    assert "Bash(python3:*)" not in allowed
    assert "Bash(pytest:*)" not in allowed
    assert "Bash(pytest tests/test_board_meeting_init.py:*)" in allowed
    assert "Bash(git:*)" in allowed


@pytest.mark.unit
def test_other_roles_keep_the_shared_python_allow_list() -> None:
    argv = ADAPTERS["claude"].build_command("do it", "/tmp/wt", model=None)
    allowed = set(argv[argv.index("--allowedTools") + 1].split(","))
    assert "Bash(python:*)" in allowed
    assert "Bash(pytest:*)" in allowed
