"""Claude /barb wrapper (#3067): UserPromptSubmit hook that injects unread rd-threads
messages addressed to this agent into context. Mention-only wake is already enforced by
``barb_cli.inbox()`` (it filters to ``to in (agent, "*")``); hop-limit loop guarding is
#3073's delivery logic, not this hook's. This hook only calls the shared entrypoint
(``barb_cli.dispatch``) and renders its result -- it never reads or prints a token."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(_ROOT)]

from scripts import barb_cli  # noqa: E402

AGENT = "claude"


def fetch_unread(cli: barb_cli.BarbCli) -> dict:
    return barb_cli.dispatch(cli, "inbox", {})


def render(result: dict) -> str:
    items = result.get("items") or []
    if not result.get("ok") or not items:
        return ""
    lines = [f"- [{i.get('kind', 'update')}] from {i.get('from', '?')}: {i.get('summary', '')}" for i in items]
    return f"## Unread rd-threads messages for @{AGENT}\n" + "\n".join(lines)


def main() -> int:
    sys.stdin.read()  # Claude Code passes session JSON on stdin; this hook ignores it
    text = render(fetch_unread(barb_cli.build_cli()))
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
