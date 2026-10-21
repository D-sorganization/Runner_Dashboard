"""Git credential helper: fetch a fresh GitHub token per ``git`` operation (#2038)."""

from __future__ import annotations

import logging
import sys
from typing import TextIO

log = logging.getLogger("dashboard.staff.github_git_credential")

from staff.github_credential_client import fetch_github_token_for_run


def run_git_credential_helper(
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> None:
    """Implement ``git credential fill`` for ``https://github.com`` via the fleet API."""
    inp = stdin or sys.stdin
    out = stdout or sys.stdout
    fields: dict[str, str] = {}
    for line in inp:
        line = line.strip()
        if not line:
            break
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key] = value
    if fields.get("protocol") != "https" or fields.get("host") != "github.com":
        return
    token = fetch_github_token_for_run()
    out.write("username=x-access-token\n")
    out.write(f"password={token}\n")


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] not in ("get", "fill"):
        return 0
    try:
        run_git_credential_helper()
    except Exception as exc:  # noqa: BLE001
        log.warning("staff github credential helper failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
