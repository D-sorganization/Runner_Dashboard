"""Bash PreToolUse hook for board-secretary launches (#4656).

Claude allow-list prefixes accept any suffix, so ``pytest … -p plugin`` and
``python -m scripts.board … && …`` match ``Bash(prefix:*)`` and still run.
``build_command`` writes a settings file that points Claude at this hook.
The hook is the boundary that actually refuses those commands.
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


def install_launch_settings(workdir: str | Path) -> Path:
    """Write the Claude settings file that hooks Bash through this module.

    Pre: ``workdir`` is the staff run directory. Post: the file exists and its
    hook command names ``staff.board_secretary_bash_hook``.
    """
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
