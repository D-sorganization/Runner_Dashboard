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
_KEY_PREFIX = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=")


def _physical_line_continues(stripped: str) -> bool:
    """True when systemd would join the next physical line (trailing backslash)."""
    if not stripped or not stripped.endswith("\\"):
        return False
    in_single = False
    in_double = False
    i = 0
    while i < len(stripped):
        ch = stripped[i]
        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == "\\" and i + 1 < len(stripped):
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue
        i += 1
    return not in_single


def _value_part(stripped: str) -> str | None:
    m = _KEY_PREFIX.match(stripped)
    if not m:
        return None
    return stripped[m.end() :]


def _validate_systemd_value(value: str) -> str | None:
    """Return an error detail when systemd would reject the value syntax."""
    if not value:
        return None
    if value[0] == '"':
        i = 1
        n = len(value)
        while i < n:
            ch = value[i]
            if ch == "\\":
                if i + 1 >= n:
                    return "unterminated double-quoted value"
                nxt = value[i + 1]
                if nxt in '"\\nrt':
                    i += 2
                    continue
                return "invalid escape in double-quoted value"
            if ch == '"':
                i += 1
                if i != n:
                    return "trailing garbage after double-quoted value"
                return None
            i += 1
        return "unterminated double-quoted value"
    if value[0] == "'":
        close = value.find("'", 1)
        if close < 0:
            return "unterminated single-quoted value"
        if close + 1 != len(value):
            return "trailing garbage after single-quoted value"
        return None
    return None


def _iter_logical_lines(text: str) -> list[tuple[int, str]]:
    """Expand systemd backslash-newline continuations; return (start_line, line)."""
    physical = text.splitlines()
    logical: list[tuple[int, str]] = []
    buf = ""
    start_line = 1
    for line_no, line in enumerate(physical, 1):
        if not buf:
            start_line = line_no
        stripped = line.strip()
        if buf:
            buf = buf[:-1] + stripped
        else:
            buf = stripped
        if _physical_line_continues(buf):
            continue
        logical.append((start_line, buf))
        buf = ""
    if buf:
        logical.append((start_line, buf))
    return logical


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

    for line_no, stripped in _iter_logical_lines(text):
        if "\x00" in stripped:
            if not any(item.get("kind") == "nul_byte" and item.get("line") == line_no for item in issues):
                issues.append(
                    {
                        "kind": "unparseable_line",
                        "line": line_no,
                        "detail": "line contains an embedded NUL byte",
                    }
                )
            continue
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        if not _ASSIGNMENT.match(stripped):
            issues.append(
                {
                    "kind": "unparseable_line",
                    "line": line_no,
                    "detail": "not a valid KEY=value assignment for systemd EnvironmentFile",
                }
            )
            continue
        value = _value_part(stripped)
        if value is None:
            issues.append(
                {
                    "kind": "unparseable_line",
                    "line": line_no,
                    "detail": "not a valid KEY=value assignment for systemd EnvironmentFile",
                }
            )
            continue
        value_err = _validate_systemd_value(value)
        if value_err:
            issues.append(
                {
                    "kind": "unparseable_line",
                    "line": line_no,
                    "detail": value_err,
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
