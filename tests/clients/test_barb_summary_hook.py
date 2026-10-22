"""Bounded Codex Stop hook for explicitly opted-in shared Barb summaries."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

_FLEET = Path(__file__).resolve().parents[2] / "clients" / "fleet"
if str(_FLEET) not in sys.path:
    sys.path.insert(0, str(_FLEET))

import barb_summary_hook  # noqa: E402

SESSION = "codex-barb-session-test"
TURN = "turn-test"


def _invoke(tmp_path: Path, payload: dict, *, allowlist: object | None = None) -> tuple[int, dict, str]:
    sessions_file = tmp_path / "sessions.json"
    if allowlist is None:
        allowlist = {"session_ids": [SESSION]}
    sessions_file.write_text(json.dumps(allowlist), encoding="utf-8")
    stdin, stdout, stderr = sys.stdin, sys.stdout, sys.stderr
    try:
        sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode("utf-8")), encoding="utf-8")
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        code = barb_summary_hook.main(["--sessions-file", str(sessions_file)])
        return code, json.loads(sys.stdout.getvalue()), sys.stderr.getvalue()
    finally:
        sys.stdin, sys.stdout, sys.stderr = stdin, stdout, stderr


def _stop_payload(**overrides: object) -> dict:
    return {
        "hook_event_name": "Stop",
        "session_id": SESSION,
        "turn_id": TURN,
        "stop_hook_active": False,
        "last_assistant_message": "Never inspect this transcript data.",
        "transcript_path": "must-not-be-opened.jsonl",
        **overrides,
    }


def test_allowed_stop_blocks_once_with_digest_tool_request_only(tmp_path: Path) -> None:
    code, result, stderr = _invoke(tmp_path, _stop_payload())

    assert code == 0
    assert result["decision"] == "block"
    assert "fleet_memory_append_session_digest" in result["reason"]
    assert '"source_product":"codex"' in result["reason"]
    assert f'"conversation_id":"{SESSION}"' in result["reason"]
    assert f'"user_message_id":"{TURN}"' in result["reason"]
    assert "concise paraphrase" in result["reason"].lower()
    assert "must-not-be-opened" not in result["reason"]
    assert "Never inspect this transcript data" not in result["reason"]
    assert "local" in result["reason"].lower()
    assert stderr == ""


def test_active_hook_and_non_stop_events_return_no_decision(tmp_path: Path) -> None:
    for payload in (_stop_payload(stop_hook_active=True), _stop_payload(hook_event_name="PreToolUse")):
        code, result, stderr = _invoke(tmp_path, payload)
        assert code == 0
        assert result == {}
        assert stderr == ""


def test_invalid_ids_unlisted_session_and_bad_allowlist_fail_open(tmp_path: Path) -> None:
    code, result, stderr = _invoke(tmp_path, _stop_payload(turn_id="bad\nturn"))
    assert code == 0
    assert result == {}
    assert "summary hook" in stderr.lower()
    assert SESSION not in stderr

    code, result, stderr = _invoke(tmp_path, _stop_payload(session_id="another-codex-session"))
    assert code == 0
    assert result == {}
    assert stderr == ""

    code, result, stderr = _invoke(tmp_path, _stop_payload(), allowlist={"session_ids": ["bad\nentry"]})
    assert code == 0
    assert result == {}
    assert "summary hook" in stderr.lower()
    assert SESSION not in stderr


def test_malformed_payload_and_oversized_stdin_are_nonblocking(tmp_path: Path) -> None:
    sessions_file = tmp_path / "sessions.json"
    sessions_file.write_text(json.dumps({"session_ids": [SESSION]}), encoding="utf-8")
    stdin, stdout, stderr = sys.stdin, sys.stdout, sys.stderr
    try:
        sys.stdin = io.TextIOWrapper(io.BytesIO(b"not-json"), encoding="utf-8")
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        assert barb_summary_hook.main(["--sessions-file", str(sessions_file)]) == 0
        assert json.loads(sys.stdout.getvalue()) == {}
        assert "summary hook" in sys.stderr.getvalue().lower()

        sys.stdin = io.TextIOWrapper(io.BytesIO(b" " * (64 * 1024 + 1)), encoding="utf-8")
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        assert barb_summary_hook.main(["--sessions-file", str(sessions_file)]) == 0
        assert json.loads(sys.stdout.getvalue()) == {}
        assert "exceeded" in sys.stderr.getvalue().lower()

        deeply_nested = ("[" * 1100 + "0" + "]" * 1100).encode("ascii")
        sys.stdin = io.TextIOWrapper(io.BytesIO(deeply_nested), encoding="utf-8")
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        assert barb_summary_hook.main(["--sessions-file", str(sessions_file)]) == 0
        assert json.loads(sys.stdout.getvalue()) == {}
        assert "summary hook" in sys.stderr.getvalue().lower()
    finally:
        sys.stdin, sys.stdout, sys.stderr = stdin, stdout, stderr


def test_allowlist_path_must_be_absolute_and_read_failure_never_blocks(tmp_path: Path) -> None:
    stdin, stdout, stderr = sys.stdin, sys.stdout, sys.stderr
    try:
        sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(_stop_payload()).encode()), encoding="utf-8")
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        assert barb_summary_hook.main(["--sessions-file", "relative.json"]) == 0
        assert json.loads(sys.stdout.getvalue()) == {}
        assert "summary hook" in sys.stderr.getvalue().lower()
    finally:
        sys.stdin, sys.stdout, sys.stderr = stdin, stdout, stderr


def test_stdin_io_error_is_nonblocking(tmp_path: Path) -> None:
    sessions_file = tmp_path / "sessions.json"
    sessions_file.write_text(json.dumps({"session_ids": [SESSION]}), encoding="utf-8")

    class BrokenBuffer:
        def read(self, _size: int) -> bytes:
            raise OSError("private stream details")

    class BrokenStdin:
        buffer = BrokenBuffer()

    stdin, stdout, stderr = sys.stdin, sys.stdout, sys.stderr
    try:
        sys.stdin = BrokenStdin()
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        assert barb_summary_hook.main(["--sessions-file", str(sessions_file)]) == 0
        assert json.loads(sys.stdout.getvalue()) == {}
        assert "could not be read" in sys.stderr.getvalue().lower()
        assert "private stream details" not in sys.stderr.getvalue()
    finally:
        sys.stdin, sys.stdout, sys.stderr = stdin, stdout, stderr
