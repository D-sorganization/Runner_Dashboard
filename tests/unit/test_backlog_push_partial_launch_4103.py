"""Tests for partial launch recovery and scoped rollback (#4103)."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import signal
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


def test_durable_per_lane_receipt_and_rollback_on_partial_failure(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    def fake_spawn(*args, **_kwargs):
        session = args[1]
        pidfile: Path = args[4]
        if session == "lane2":
            raise RuntimeError("lane2 spawn boom")
        pidfile.write_text("101", encoding="utf-8")
        return 101

    plan = [
        {"role": "supervisor", "session": "sup1", "prompt": "p1"},
        {"role": "lane", "session": "lane2", "prompt": "p2"},
    ]

    with patch.dict(os.environ, {"RUNNER_DASHBOARD_ROOT": str(_ROOT)}):
        with patch.object(spawn, "_spawn_one", side_effect=fake_spawn):
            with patch("os.kill") as mock_kill:
                with patch.object(spawn.sys, "stdin", io.StringIO(json.dumps(plan))):
                    with pytest.raises(RuntimeError, match="lane2 spawn boom"):
                        spawn.main([str(log_dir)])

                # Earlier lane sup1 was killed cleanly
                mock_kill.assert_called_with(101, signal.SIGTERM)

    # Durable per-lane receipt exists
    receipt_file = log_dir / "sup1.receipt.json"
    assert receipt_file.is_file()
    receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert receipt_data["session"] == "sup1"
    assert receipt_data["pid"] == 101
    assert receipt_data["status"] == "rolled_back"

    # sup1 pidfile was cleaned up upon successful termination
    assert not (log_dir / "sup1.pid").exists()


def test_retry_recovers_already_running_lane_without_relaunch(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    # Pre-existing running lane
    pidfile = log_dir / "sup1.pid"
    pidfile.write_text("202", encoding="utf-8")
    receipt_file = log_dir / "sup1.receipt.json"
    receipt_file.write_text(
        json.dumps({"session": "sup1", "role": "supervisor", "pid": 202, "status": "active"}),
        encoding="utf-8",
    )

    spawn_calls: list[str] = []

    def fake_spawn(*args, **_kwargs):
        session = args[1]
        spawn_calls.append(session)
        pidfile_arg: Path = args[4]
        pidfile_arg.write_text("203", encoding="utf-8")
        return 203

    plan = [
        {"role": "supervisor", "session": "sup1", "prompt": "p1"},
        {"role": "lane", "session": "lane2", "prompt": "p2"},
    ]

    with patch.dict(os.environ, {"RUNNER_DASHBOARD_ROOT": str(_ROOT)}):
        with patch.object(spawn, "_spawn_one", side_effect=fake_spawn):
            with patch.object(spawn, "_is_active_process", side_effect=lambda p: p == 202):
                with patch.object(spawn.sys, "stdin", io.StringIO(json.dumps(plan))):
                    out_buf = io.StringIO()
                    with patch.object(spawn.sys, "stdout", out_buf):
                        exit_code = spawn.main([str(log_dir)])
                    assert exit_code == 0
                    result = json.loads(out_buf.getvalue())
                    assert result["supervisor"] == 202
                    assert result["lane1"] == 203

    # sup1 was recovered, NOT relaunched! Only lane2 called _spawn_one
    assert "sup1" not in spawn_calls
    assert spawn_calls == ["lane2"]


def test_cleanup_never_targets_unrelated_pids() -> None:
    log_dir = Path("/nonexistent/log/dir")
    with patch("os.kill") as mock_kill:
        # PIDs <= 1 (system, init, kernel, negative) must never be targeted
        spawn._rollback_launched(log_dir, [("s0", 0), ("s1", 1), ("sneg", -5)])
        mock_kill.assert_not_called()


def test_partial_cleanup_failure_retains_recovery_evidence(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    pidfile = log_dir / "lane1.pid"
    pidfile.write_text("303", encoding="utf-8")
    receipt_file = log_dir / "lane1.receipt.json"
    receipt_file.write_text(
        json.dumps({"session": "lane1", "role": "lane", "pid": 303, "status": "active"}),
        encoding="utf-8",
    )

    def failing_kill(pid, sig):
        if pid == 303:
            raise OSError("permission denied / zombie")

    with patch("os.kill", side_effect=failing_kill):
        spawn._rollback_launched(log_dir, [("lane1", 303)])

    # Evidence retained: pidfile NOT unlinked because termination failed
    assert pidfile.is_file()
    assert pidfile.read_text(encoding="utf-8").strip() == "303"

    # Recovery evidence file exists documenting the partial failure
    evidence_file = log_dir / "recovery_evidence.json"
    assert evidence_file.is_file()
    evidence_data = json.loads(evidence_file.read_text(encoding="utf-8"))
    assert "lane1" in evidence_data["cleanup_failures"]
    assert evidence_data["cleanup_failures"]["lane1"]["pid"] == 303
