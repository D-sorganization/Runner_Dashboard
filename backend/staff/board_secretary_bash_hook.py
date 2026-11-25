"""Bash PreToolUse hook for board-secretary launches (#4656).

Claude allow-list prefixes accept any suffix, so ``pytest … -p plugin`` and
``python -m scripts.board … && …`` match ``Bash(prefix:*)`` and still run.
``build_command`` names a settings file that points Claude at this hook and
writes that file only when the run directory already exists. The hook is the
boundary that actually refuses those commands.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

from staff.adapter_policies import UNATTENDED_SHELL_ALLOW, board_secretary_archive_command_allowed

_INTERPRETERS = frozenset({"python", "python3", "pytest"})
_SHELL_META = ("&&", "||", ";", "|", "`", "$(", "\n", ">", "<")
_OTHER_PREFIXES = tuple(prefix for prefix in UNATTENDED_SHELL_ALLOW if prefix not in _INTERPRETERS)
_PLAN_WORKDIR = "<workdir>"


def _is_plan_placeholder(workdir: str | Path) -> bool:
    """True for the plan-time workdir token, which is not a directory."""
    text = str(workdir)
    return text == _PLAN_WORKDIR or Path(text).name == _PLAN_WORKDIR


def launched_command_allowed(command: str) -> bool:
    """Return whether a board-secretary Bash tool call may run.

    Pre: ``command`` is the tool command string. Post: shell syntax is refused;
    python and pytest run only when :func:`board_secretary_archive_command_allowed`
    says so; other commands must match a non-interpreter unattended prefix.
    """
    text = command.strip()
    if not text or any(meta in text for meta in _SHELL_META):
        return False
    try:
        parts = shlex.split(text, posix=True)
    except ValueError:
        return False
    if not parts:
        return False
    if parts[0] in _INTERPRETERS:
        return board_secretary_archive_command_allowed(text)
    return any(text == prefix or text.startswith(prefix + " ") for prefix in _OTHER_PREFIXES)


def launch_settings_path(workdir: str | Path) -> Path:
    """Return the Claude settings path, writing it only for a real directory.

    Pre: ``workdir`` is the plan placeholder ``<workdir>`` or a run directory.
    Post: the placeholder is never created. An existing directory receives the hook file.
    """
    path = Path(workdir)
    if _is_plan_placeholder(path) or not path.is_dir():
        return path / ".staff" / "board-secretary-claude.json"
    return install_launch_settings(path)


def install_launch_settings(workdir: str | Path) -> Path:
    """Write the Claude settings file that hooks Bash through this module.

    Pre: ``workdir`` is an existing staff run directory, not the plan placeholder.
    Post: the file exists and its hook command names ``staff.board_secretary_bash_hook``.
    """
    if _is_plan_placeholder(workdir):
        raise ValueError("refusing to create the plan placeholder workdir")
    backend = Path(__file__).resolve().parents[1]
    command = f"PYTHONPATH={shlex.quote(str(backend))} {shlex.quote(sys.executable)} -m staff.board_secretary_bash_hook"
    payload = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": command}],
                }
            ]
        }
    }
    path = Path(workdir) / ".staff" / "board-secretary-claude.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _command_from_payload(raw: str) -> str:
    try:
        body = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return ""
    if not isinstance(body, dict):
        return ""
    tool_input = body.get("tool_input")
    if isinstance(tool_input, dict):
        return str(tool_input.get("command") or tool_input.get("cmd") or "")
    return str(body.get("command") or "")


def main() -> int:
    """Exit 0 when the Bash command is allowed, 2 when Claude must block it."""
    command = _command_from_payload(sys.stdin.read())
    if launched_command_allowed(command):
        return 0
    sys.stderr.write("board-secretary bash hook refused the command\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
