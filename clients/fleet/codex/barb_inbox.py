"""Codex UserPromptSubmit hook: inject rd-barb inbox only for explicit mentions."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    event = json.load(sys.stdin)
    prompt = str(event.get("prompt", ""))
    if "@barb" not in prompt.casefold():
        return 0
    cli = Path(__file__).with_name("rd_threads_cli").read_text(encoding="utf-8").strip()
    cli = Path(cli)
    if not cli.is_file():
        return 0
    result = subprocess.run(
        [sys.executable, str(cli), "inbox", "{}"], capture_output=True, text=True, timeout=2.0, check=False
    )
    if result.returncode:
        return 0
    inbox = json.loads(result.stdout)
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": json.dumps(inbox.get("items", []), ensure_ascii=False),
                }
            }
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
