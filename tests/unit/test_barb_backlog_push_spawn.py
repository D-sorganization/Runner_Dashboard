"""Tests for detached backlog-push spawn (#4098). Refs #4103, #4105."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "backlog_push_spawn", _ROOT / "scripts/barb-worker/backlog-push-spawn.py"
)
assert _spec and _spec.loader
spawn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spawn)


def test_spawn_env_strips_bot_token_from_lane_env() -> None:
    env = spawn._lane_env()
    assert "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env


def test_lane_script_refreshes_gh_without_exporting_token() -> None:
    text = (_ROOT / "scripts/barb-worker/backlog-push-lane.sh").read_text(encoding="utf-8")
    assert "unset GH_TOKEN GITHUB_TOKEN" in text
    assert "gh api user" in text
    assert "wait \"${WORKER_PID}\"" in text
    assert "stop_refresh" in text
    assert 'echo $GH_TOKEN' not in text and 'GH_TOKEN="' not in text


def test_partial_launch_rolls_back_earlier_lane(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    calls: list[str] = []

    def fake_spawn(*_a, **_k):
        session = _a[1]
        calls.append(session)
        if session == "lane2":
            raise RuntimeError("spawn failed")
        pidfile: Path = _a[4]
        pidfile.write_text("42", encoding="utf-8")
        return 42

    plan = [
        {"role": "supervisor", "session": "sup", "prompt": "p1"},
        {"role": "lane", "session": "lane2", "prompt": "p2"},
    ]
    with patch.dict(os.environ, {"RUNNER_DASHBOARD_ROOT": str(_ROOT)}):
        with patch.object(spawn, "_spawn_one", side_effect=fake_spawn):
            with patch.object(spawn, "_rollback_launched") as rollback:
                with patch.object(spawn.sys, "stdin", io.StringIO(json.dumps(plan))):
                    with pytest.raises(RuntimeError, match="spawn failed"):
                        spawn.main([str(log_dir)])
                rollback.assert_called_once()
                rolled = rollback.call_args[0][1]
                assert rolled == [("sup", 42)]


def test_predecessor_tests_do_not_import_relay_module() -> None:
    import ast

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imports = {node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)}
    assert "barb_backlog_push_relay" not in imports
