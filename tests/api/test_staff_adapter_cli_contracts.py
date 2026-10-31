"""Provider CLI contracts for Staff Hub adapters (issue #1249).

Pins the flags and stream shapes of the installed CLIs so a CLI upgrade that
changes them fails here instead of in a scheduled run: codex 0.156 removed
``--full-auto``; agy reports its answer as ``result.response``.
"""

from __future__ import annotations

import json

import pytest
from staff import adapters as adapters_mod


@pytest.mark.unit
def test_codex_uses_current_unattended_flags() -> None:
    argv = adapters_mod.ADAPTERS["codex"].build_command("do it", "/tmp/wt", model=None)
    assert argv[:2] == ["codex", "exec"]
    assert "--full-auto" not in argv  # removed in codex 0.156
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"  # never the approvals bypass (#1586)
    assert "--skip-git-repo-check" in argv  # ad-hoc runs have no repo
    assert argv[-1] == "do it"


@pytest.mark.unit
def test_agy_result_response_carries_staff_result_and_usage() -> None:
    agy = adapters_mod.ADAPTERS["antigravity"]
    line = json.dumps(
        {
            "event": "result",
            "result": {
                "status": "SUCCESS",
                "response": "OK\n\nSTAFF_RESULT: ok\n",
                "usage": {"input_tokens": 17053, "output_tokens": 536, "total_tokens": 17589},
            },
        }
    )
    ev = agy.parse_line(line)
    assert ev["kind"] == "result"
    assert "STAFF_RESULT: ok" in ev["text"]
    assert ev["usage"] == {"input_tokens": 17053, "output_tokens": 536, "total_tokens": 17589}


@pytest.mark.unit
def test_agy_unattended_argv_includes_auto_mode_and_model() -> None:
    agy = adapters_mod.ADAPTERS["antigravity"]
    assert agy.unattended is True
    argv = agy.build_command("do it", "/tmp/wt", model="gemini-3.8-flash-high")
    assert argv[0] == "agy"
    assert "--mode" in argv and argv[argv.index("--mode") + 1] == "auto"
    assert "--model" in argv and argv[argv.index("--model") + 1] == "gemini-3.8-flash-high"
    assert "--dangerously-skip-permissions" not in argv


@pytest.mark.unit
def test_agy_session_argv_uses_conversation() -> None:
    argv = adapters_mod.ADAPTERS["antigravity"].build_command("continue", "/tmp/wt", session_id="abc")
    assert argv[argv.index("--conversation") + 1] == "abc"
    assert "--resume" not in argv


@pytest.mark.unit
def test_agy_init_event_is_not_mistaken_for_text() -> None:
    agy = adapters_mod.ADAPTERS["antigravity"]
    ev = agy.parse_line(json.dumps({"event": "init", "init": {"cwd": "/w", "tools": ["ask_question"]}}))
    assert ev["kind"] == "init" and ev["text"] == ""


@pytest.mark.unit
def test_claude_build_command_passes_schema_inline(tmp_path) -> None:
    # Claude's ``--json-schema`` takes the schema itself, not a file path (#3619).
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
    argv = adapters_mod.ADAPTERS["claude"].build_command("do it", str(tmp_path), output_schema=schema)
    assert argv[argv.index("--json-schema") + 1] == json.dumps(schema)


@pytest.mark.unit
def test_codex_build_command_passes_schema_as_file(tmp_path) -> None:
    # Codex's ``--output-schema`` takes a file path (#3619).
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
    argv = adapters_mod.ADAPTERS["codex"].build_command("do it", str(tmp_path), output_schema=schema)
    schema_path = argv[argv.index("--output-schema") + 1]
    assert json.loads(open(schema_path).read()) == schema


@pytest.mark.unit
def test_build_command_omits_schema_flags_by_default() -> None:
    # Additive: no caller opts in, so existing free-text STAFF_RESULT: behavior is unchanged.
    assert "--json-schema" not in adapters_mod.ADAPTERS["claude"].build_command("do it", "/tmp/wt")
    assert "--output-schema" not in adapters_mod.ADAPTERS["codex"].build_command("do it", "/tmp/wt")


@pytest.mark.unit
def test_antigravity_build_command_ignores_output_schema() -> None:
    # Only claude- and codex-style adapters know a native schema flag; others no-op (#3619).
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
    argv = adapters_mod.ADAPTERS["antigravity"].build_command(
        "do it", "/tmp/wt", model="gemini-3.8-flash-high", output_schema=schema
    )
    assert "--json-schema" not in argv
    assert "--output-schema" not in argv
