"""Exact antigravity chat argv, incl. the long-prompt stdin path (#2744).

``agy`` on the hub is a Windows ``agy.exe`` reached through WSL interop; an argv longer than the
~32 K Windows command line fails with ``agy: Invalid argument``. Long panel prompts must go on stdin.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from staff.adapter_policies import PERMISSION_BYPASS_FLAGS
from staff.adapters import AGY_MAX_ARGV_PROMPT_CHARS, get_adapter


@pytest.mark.unit
def test_antigravity_chat_argv_default_model_exact() -> None:
    adapter = get_adapter("antigravity")
    assert adapter.chat_argv("hello", "/tmp/w") == [
        "agy",
        "--print",
        "hello",
        "--output-format",
        "stream-json",
        "--add-dir",
        "/tmp/w",
        "--mode",
        "plan",
        "--model",
        "gemini-3.8-flash-high",
    ]
    assert adapter.chat_stdin("hello") is None


@pytest.mark.unit
def test_antigravity_chat_argv_explicit_model_exact() -> None:
    argv = get_adapter("antigravity").chat_argv("hello", "/tmp/w", model="gemini-4-argon-high")
    assert argv[-2:] == ["--model", "gemini-4-argon-high"]
    assert argv[:3] == ["agy", "--print", "hello"]
    assert not PERMISSION_BYPASS_FLAGS & set(argv)


@pytest.mark.unit
def test_antigravity_long_prompt_goes_on_stdin_not_argv() -> None:
    adapter = get_adapter("antigravity")
    prompt = "x" * (AGY_MAX_ARGV_PROMPT_CHARS + 1)
    argv = adapter.chat_argv(prompt, "/tmp/w", model="gemini-4-argon-high")
    assert argv == [
        "agy",
        "--output-format",
        "stream-json",
        "--add-dir",
        "/tmp/w",
        "--mode",
        "plan",
        "--model",
        "gemini-4-argon-high",
    ]
    assert adapter.chat_stdin(prompt) == prompt
    assert sum(len(a) for a in argv) < 1000


@pytest.mark.unit
def test_antigravity_long_prompt_resume_keeps_plan_mode() -> None:
    adapter = get_adapter("antigravity")
    argv = adapter.chat_argv("y" * (AGY_MAX_ARGV_PROMPT_CHARS + 1), "/w", session_id="s1")
    assert argv[argv.index("--mode") + 1] == "plan"
    assert argv[argv.index("--conversation") + 1] == "s1"


@pytest.mark.unit
def test_other_providers_never_use_stdin() -> None:
    big = "z" * (AGY_MAX_ARGV_PROMPT_CHARS + 1)
    for pid in ("claude", "codex", "gemini", "cursor-agent"):
        assert get_adapter(pid).chat_stdin(big) is None


@pytest.mark.unit
def test_spawn_cli_process_pipes_stdin_text(tmp_path: Path) -> None:
    import os
    import sys

    from staff.chat_streaming import spawn_cli_process

    proc = spawn_cli_process(
        cmd=[sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        cwd=str(tmp_path),
        env={**os.environ},
        stdin_text="long prompt",
    )
    out, _ = proc.communicate(timeout=30)
    assert out == "LONG PROMPT"
