#!/usr/bin/env python3
"""Inspect and safely rewrite the runner-dashboard systemd EnvironmentFile.

systemd skips the entire EnvironmentFile when any line is unparseable (for example
leading NUL bytes after a Windows/WSL unclean shutdown). Fleet scripts use this
module before refreshing secrets or restarting services.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_ASSIGNMENT = re.compile(r"^(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*=")


def inspect_env_file(path: Path) -> dict[str, Any]:
    """Return validation metadata for a secrets env file (never logs secret values)."""
    if not path.is_file():
        return {"ok": True, "missing": True, "path": str(path), "issues": []}

    raw = path.read_bytes()
    issues: list[dict[str, Any]] = []

    if b"\x00" in raw:
        idx = raw.index(b"\x00")
        line_no = raw[:idx].count(b"\n") + 1
        issues.append(
            {
                "kind": "nul_byte",
                "line": line_no,
                "detail": (
                    "NUL byte in env file; systemd skips the entire EnvironmentFile "
                    "(loopback-allow and allowed-hosts will not load)"
                ),
            }
        )

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        issues.append({"kind": "encoding", "line": None, "detail": f"invalid UTF-8: {exc}"})
        text = raw.decode("utf-8", errors="replace")

    for line_no, line in enumerate(text.splitlines(), 1):
        if "\x00" in line:
            if not any(item.get("kind") == "nul_byte" and item.get("line") == line_no for item in issues):
                issues.append(
                    {
                        "kind": "unparseable_line",
                        "line": line_no,
                        "detail": "line contains an embedded NUL byte",
                    }
                )
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not _ASSIGNMENT.match(stripped):
            issues.append(
                {
                    "kind": "unparseable_line",
                    "line": line_no,
                    "detail": "not a valid KEY=value assignment for systemd EnvironmentFile",
                }
            )

    return {"ok": not issues, "missing": False, "path": str(path), "issues": issues}


def log_issues_report(report: dict[str, Any], *, prefix: str = "runner-dashboard-env") -> None:
    if report.get("missing") or report.get("ok"):
        return
    for issue in report.get("issues") or []:
        line = issue.get("line")
        where = f"line {line}" if line else "file"
        log.warning("[%s] CORRUPT env file %s: %s — %s", prefix, report.get("path"), where, issue.get("detail"))


def _read_text_stripping_nul(raw: bytes) -> str:
    cleaned = raw.replace(b"\x00", b"")
    return cleaned.decode("utf-8", errors="replace")


def atomic_set_gh_token(path: Path, token: str) -> None:
    """Rewrite GH_TOKEN atomically; preserve other assignments and mode (default 0600)."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    mode = 0o600
    kept_lines: list[str] = []
    if path.is_file():
        mode = path.stat().st_mode & 0o777
        text = _read_text_stripping_nul(path.read_bytes())
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("GH_TOKEN=") or stripped.startswith("export GH_TOKEN="):
                continue
            kept_lines.append(line.rstrip("\r"))

    kept_lines.append(f"GH_TOKEN={token}")
    body = "\n".join(kept_lines) + "\n"

    tmp = path.with_name(f"{path.name}.refresh.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    os.chmod(tmp, mode)
    os.replace(tmp, path)
    dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default=str(Path.home() / ".config/runner-dashboard/env"),
        help="Path to runner-dashboard secrets env file",
    )
    parser.add_argument("--json", action="store_true", help="Emit inspect_env_file JSON on stdout")
    parser.add_argument("--warn", action="store_true", help="Log issues to stderr when corrupt")
    parser.add_argument(
        "--set-gh-token-stdin",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    path = Path(args.env_file).expanduser()
    if args.set_gh_token_stdin:
        token = sys.stdin.read().strip()
        if not token:
            log.error("refusing to write empty GH_TOKEN")
            return 1
        atomic_set_gh_token(path, token)
        return 0

    report = inspect_env_file(path)
    if args.warn and not report.get("ok"):
        logging.basicConfig(level=logging.WARNING, format="%(message)s")
        log_issues_report(report)

    if args.json:
        print(json.dumps(report))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
