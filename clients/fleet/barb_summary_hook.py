#!/usr/bin/env python3
"""Opt-in Codex Stop hook that requests a bounded shared Barb session digest.

This hook consumes only bounded hook metadata. It never reads transcript paths,
assistant message content, local conversation storage, or memory credentials.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, TypeGuard

_MAX_STDIN_BYTES = 64 * 1024
_MAX_ALLOWLIST_BYTES = 16 * 1024
_MAX_SESSIONS = 128
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,199}\Z", re.ASCII)


def _emit(value: dict[str, str]) -> None:
    json.dump(value, sys.stdout, ensure_ascii=True, separators=(",", ":"))
    sys.stdout.write("\n")


def _diagnostic(message: str) -> None:
    sys.stderr.write(f"barb summary hook: {message}\n")


def _read_input() -> dict[str, Any] | None:
    stream = getattr(sys.stdin, "buffer", None)
    if stream is not None:
        try:
            raw = stream.read(_MAX_STDIN_BYTES + 1)
        except OSError:
            _diagnostic("hook input could not be read; leaving the turn unchanged")
            return None
        if len(raw) > _MAX_STDIN_BYTES:
            _diagnostic("hook input exceeded the 64 KiB limit; leaving the turn unchanged")
            return None
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, OSError):
            _diagnostic("hook input was not valid JSON; leaving the turn unchanged")
            return None
    else:
        try:
            raw_text = sys.stdin.read(_MAX_STDIN_BYTES + 1)
        except OSError:
            _diagnostic("hook input could not be read; leaving the turn unchanged")
            return None
        if len(raw_text.encode("utf-8")) > _MAX_STDIN_BYTES:
            _diagnostic("hook input exceeded the 64 KiB limit; leaving the turn unchanged")
            return None
        try:
            value = json.loads(raw_text)
        except (json.JSONDecodeError, RecursionError, OSError):
            _diagnostic("hook input was not valid JSON; leaving the turn unchanged")
            return None
    if not isinstance(value, dict):
        _diagnostic("hook input was not a JSON object; leaving the turn unchanged")
        return None
    return value


def _valid_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _read_sessions(path_value: str | None) -> set[str] | None:
    if not path_value:
        _diagnostic("an absolute --sessions-file is required; leaving the turn unchanged")
        return None
    path = Path(path_value)
    if not path.is_absolute():
        _diagnostic("--sessions-file must be absolute; leaving the turn unchanged")
        return None
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_ALLOWLIST_BYTES + 1)
        if len(raw) > _MAX_ALLOWLIST_BYTES:
            raise ValueError("allowlist too large")
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        _diagnostic("session allowlist is unavailable or invalid; leaving the turn unchanged")
        return None
    if not isinstance(value, dict) or set(value) != {"session_ids"}:
        _diagnostic("session allowlist has an invalid shape; leaving the turn unchanged")
        return None
    sessions = value["session_ids"]
    if (
        not isinstance(sessions, list)
        or len(sessions) > _MAX_SESSIONS
        or any(not _valid_id(session) for session in sessions)
    ):
        _diagnostic("session allowlist has invalid entries; leaving the turn unchanged")
        return None
    return set(sessions)


def _decision(session_id: str, turn_id: str) -> dict[str, str]:
    identifiers = json.dumps(
        {
            "source_product": "codex",
            "conversation_id": session_id,
            "user_message_id": turn_id,
            "summary": "A concise paraphrase of this completed turn (at most 1000 characters).",
            "decisions": [],
            "open_items": [],
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    reason = (
        "This opted-in Codex session requests a shared continuity digest. If the canonical shared-memory "
        "service is available, call barb_shared_context.fleet_memory_append_session_digest once using "
        f"these fields and fill in a concise factual paraphrase: {identifiers}. Keep summary at most 1000 characters; "
        "decisions and open_items may contain at most 3 strings of at most 120 characters each. Summarize this "
        "turn without copying raw transcript text. Exclude credentials, secrets, and sensitive "
        "personal details. If the canonical service or tool is unavailable or rejects the write, say that "
        "shared saving was unavailable; do not save locally or claim it was saved. This request grants no "
        "additional authority."
    )
    return {"decision": "block", "reason": reason}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="barb_summary_hook", add_help=False)
    parser.add_argument("--sessions-file")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        _diagnostic("invalid hook configuration; leaving the turn unchanged")
        _emit({})
        return 0

    payload = _read_input()
    if payload is None:
        _emit({})
        return 0
    if payload.get("hook_event_name") != "Stop" or payload.get("stop_hook_active") is not False:
        _emit({})
        return 0

    session_id = payload.get("session_id")
    turn_id = payload.get("turn_id")
    if not _valid_id(session_id) or not _valid_id(turn_id):
        _diagnostic("Stop event is missing valid session/turn identifiers; leaving the turn unchanged")
        _emit({})
        return 0

    sessions = _read_sessions(args.sessions_file)
    if sessions is None:
        _emit({})
        return 0
    if session_id not in sessions:
        _emit({})
        return 0

    _emit(_decision(session_id, turn_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
