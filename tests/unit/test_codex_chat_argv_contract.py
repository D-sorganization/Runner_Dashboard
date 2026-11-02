"""Codex chat argv and JSON stream contract (#3595)."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from staff.adapters import get_adapter


def test_fresh_chat_argv_has_no_deprecated_session_flag() -> None:
    argv = get_adapter("codex").chat_argv("hello", "/tmp/w", model="gpt-6-luna")
    assert "--session" not in argv
    assert "--json" in argv


def test_resume_chat_argv_uses_exec_resume_subcommand() -> None:
    argv = get_adapter("codex").chat_argv("again", "/tmp/w", session_id="sess-1", model="gpt-6-luna")
    assert argv[1:4] == ["exec", "resume", "--skip-git-repo-check"]
    idx = argv.index("sess-1")
    assert argv[idx + 1] == "again"


def test_codex_json_fixture_yields_assistant_text_and_thread_id() -> None:
    adapter = get_adapter("codex")
    started = json.dumps({"type": "thread.started", "thread_id": "thread-abc"})
    message = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Here is the answer."},
        }
    )
    events = [adapter.parse_line(started), adapter.parse_line(message)]
    assert events[0]["raw"]["thread_id"] == "thread-abc"
    assert "answer" in events[1]["text"]


@pytest.mark.skipif(shutil.which("codex") is None, reason="codex CLI not installed")
def test_installed_codex_exec_help_lists_resume_not_session_flag() -> None:
    proc = subprocess.run(  # noqa: S603
        ["codex", "exec", "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr[:500]
    help_text = proc.stdout.lower()
    assert "resume" in help_text
    assert "--session" not in help_text
