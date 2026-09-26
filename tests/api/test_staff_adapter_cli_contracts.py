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
def test_agy_init_event_is_not_mistaken_for_text() -> None:
    agy = adapters_mod.ADAPTERS["antigravity"]
    ev = agy.parse_line(json.dumps({"event": "init", "init": {"cwd": "/w", "tools": ["ask_question"]}}))
    assert ev["kind"] == "init" and ev["text"] == ""
