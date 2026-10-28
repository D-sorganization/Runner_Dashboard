"""Install and invoke the Codex /barb prompt through the shared rd-threads CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

_PROVIDER_DIR = Path(__file__).resolve().parent
_ROOT = _PROVIDER_DIR.parents[2]
_ENTRYPOINT = _ROOT / "scripts" / "barb_cli.py"


def install(codex_home: Path | None = None) -> Path:
    """Install the versioned prompt in the selected Codex profile."""
    home = codex_home or Path.home() / ".codex"
    target = home / "prompts" / "barb.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text((_PROVIDER_DIR / "barb_prompt.md").read_text(encoding="utf-8"), encoding="utf-8")
    hook_script = home / "hooks" / "barb_inbox.py"
    hook_script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_PROVIDER_DIR / "barb_inbox.py", hook_script)
    (hook_script.parent / "rd_threads_cli").write_text(str(_ENTRYPOINT), encoding="utf-8")
    config_path = home / "hooks.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {"hooks": {}}
    hooks = config.setdefault("hooks", {})
    prompt_hooks = hooks.setdefault("UserPromptSubmit", [])
    hook_command = f'{sys.executable} "{hook_script}"'
    if not any(
        hook.get("command") == hook_command
        for group in prompt_hooks
        if isinstance(group, dict)
        for hook in group.get("hooks", [])
        if isinstance(hook, dict)
    ):
        prompt_hooks.append({"hooks": [{"type": "command", "command": hook_command, "timeout": 2}]})
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return target


def _invoke(verb: str, payload: dict[str, str]) -> str:
    completed = subprocess.run(
        [sys.executable, str(_ENTRYPOINT), verb, json.dumps(payload, separators=(",", ":"))],
        capture_output=True,
        check=False,
        text=True,
        timeout=2.0,
    )
    if completed.returncode:
        raise RuntimeError("shared rd-threads entrypoint failed")
    return completed.stdout


def dispatch(arguments: Sequence[str], *, invoke: Callable[[str, dict[str, str]], str] = _invoke) -> str:
    """Send one message or chat continuation; return its correlation id."""
    parts = list(arguments)
    if not parts:
        raise ValueError("/barb requires a message or chat")
    is_chat = parts[0] == "chat"
    if is_chat:
        parts = parts[1:]
    message = " ".join(parts).strip()
    if not message and not is_chat:
        raise ValueError("/barb chat requires a message")
    payload = {
        "to": "barb",
        "kind": "update",
        "summary": "Codex Barb chat" if is_chat else " ".join(message.splitlines()).strip()[:200],
        "body": message,
    }
    response = json.loads(invoke("send", payload))
    correlation_id = response.get("correlation_id")
    if response.get("ok") is not True or not isinstance(correlation_id, str) or not correlation_id:
        raise RuntimeError("shared rd-threads entrypoint returned no correlation id")
    return correlation_id


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--install":
        sys.stdout.write(f"{install()}\n")
        raise SystemExit(0)
    sys.stdout.write(f"{dispatch(sys.argv[1:])}\n")
