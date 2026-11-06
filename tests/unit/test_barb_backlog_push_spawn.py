"""Tests for detached backlog-push spawn (#4098). Refs #4103, #4105."""

from __future__ import annotations

import ast
import importlib
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
    assert 'wait "${WORKER_PID}"' in text
    assert "stop_refresh" in text
    assert "echo $GH_TOKEN" not in text and 'GH_TOKEN="' not in text


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


def _extract_imports(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
            for alias in node.names:
                imports.add(alias.name)
    return imports


def test_predecessor_tests_do_not_import_relay_module() -> None:
    """Audit gate #4105: spawn tests and spawn script must not import downstream relay."""
    forbidden = {"barb_backlog_push_relay", "scripts.barb_backlog_push_relay"}
    for target in [Path(__file__), _ROOT / "scripts/barb-worker/backlog-push-spawn.py"]:
        tree = ast.parse(target.read_text(encoding="utf-8"))
        imports = _extract_imports(tree)
        assert not (imports & forbidden), f"{target.name} imports forbidden relay modules: {imports & forbidden}"

    # Regression check: verify the AST check correctly catches PR #4100's historical import
    historical_code = (
        "import json\nimport subprocess\nimport barb_backlog_push_relay as relay\n"
        "from scripts.barb_backlog_push_relay import BacklogPushRelay\n"
    )
    detected = _extract_imports(ast.parse(historical_code)) & forbidden
    assert detected == forbidden, f"Historical code imports must be detected: {detected}"


def test_spawn_runs_cleanly_with_relay_module_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Predecessor-only spawn runs cleanly when relay is poisoned/absent in sys.modules (#4105)."""
    monkeypatch.setitem(sys.modules, "barb_backlog_push_relay", None)
    monkeypatch.setitem(sys.modules, "scripts.barb_backlog_push_relay", None)

    # Confirm that relay imports are blocked and raise ModuleNotFoundError
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("barb_backlog_push_relay")

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("scripts.barb_backlog_push_relay")

    # Reload spawn module in isolated environment with blocked relay
    spec = importlib.util.spec_from_file_location(
        "backlog_push_spawn_isolated", _ROOT / "scripts/barb-worker/backlog-push-spawn.py"
    )
    assert spec and spec.loader
    spawn_isolated = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(spawn_isolated)

    # Verify lane environment isolation
    env = spawn_isolated._lane_env()
    assert "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env

    # Verify main spawn execution with launch manifest generation
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    plan = [
        {"role": "supervisor", "session": "sup-iso", "prompt": "prompt-sup"},
        {"role": "lane", "session": "lane-iso", "prompt": "prompt-lane"},
    ]

    def fake_spawn_one(
        lane_sh: Path,
        session: str,
        log_path: Path,
        prompt_path: Path,
        pidfile: Path,
        env: dict[str, str],
    ) -> int:
        pid = 9101 if session == "sup-iso" else 9102
        pidfile.write_text(str(pid), encoding="utf-8")
        return pid

    with patch.dict(os.environ, {"RUNNER_DASHBOARD_ROOT": str(_ROOT)}):
        with patch.object(spawn_isolated, "_spawn_one", side_effect=fake_spawn_one):
            with patch.object(spawn_isolated.sys, "stdin", io.StringIO(json.dumps(plan))):
                ret = spawn_isolated.main([str(log_dir)])
                assert ret == 0

    manifest_file = log_dir / "launch_manifest.json"
    assert manifest_file.exists()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert manifest["pids"] == {"supervisor": 9101, "lane1": 9102}
    assert manifest["sessions"] == ["sup-iso", "lane-iso"]

    # Verify rollback under failure operates without relay
    rollback_dir = tmp_path / "rollback_logs"
    rollback_dir.mkdir()
    signalled_pids: list[int] = []
    with patch.object(spawn_isolated.os, "kill", lambda pid, sig: signalled_pids.append(pid)):
        (rollback_dir / "sup-iso.pid").write_text("9101", encoding="utf-8")
        spawn_isolated._rollback_launched(rollback_dir, [("sup-iso", 9101)])
        assert signalled_pids == [9101]
        assert not (rollback_dir / "sup-iso.pid").exists()
