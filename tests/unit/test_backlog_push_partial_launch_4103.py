"""Tests for partial launch recovery, scoped rollback, and adversarial failure modes (#4103)."""

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

_SIGKILL = getattr(signal, "SIGKILL", 9)
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location("bpush_spawn", _ROOT / "scripts/barb-worker/backlog-push-spawn.py")
assert _spec and _spec.loader
spawn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spawn)


def _make_kill(alive: dict[int, bool], log: list[tuple[int, int]]):
    def fake_kill(pid: int, sig: int) -> None:
        log.append((pid, sig))
        if sig == 0:
            if not alive.get(pid, False):
                raise ProcessLookupError
            return
        if sig in (signal.SIGTERM, _SIGKILL):
            alive[pid] = False

    return fake_kill


def test_durable_per_lane_receipt_and_rollback_on_partial_failure(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    alive, kill_log = {101: True}, []

    def fake_spawn(*a, **_):
        if a[1] == "lane2":
            raise RuntimeError("lane2 boom")
        a[4].write_text("101", encoding="utf-8")
        return 101

    plan = [
        {"role": "supervisor", "session": "sup1", "prompt": "p1"},
        {"role": "lane", "session": "lane2", "prompt": "p2"},
    ]
    with (
        patch.dict(os.environ, {"RUNNER_DASHBOARD_ROOT": str(_ROOT)}),
        patch.object(spawn, "_spawn_one", side_effect=fake_spawn),
        patch("os.kill", side_effect=_make_kill(alive, kill_log)),
        patch.object(spawn.sys, "stdin", io.StringIO(json.dumps(plan))),
    ):
        with pytest.raises(RuntimeError, match="lane2 boom"):
            spawn.main([str(log_dir)])

    assert (101, signal.SIGTERM) in kill_log and not alive[101]
    rec = json.loads((log_dir / "sup1.receipt.json").read_text(encoding="utf-8"))
    assert rec["session"] == "sup1" and rec["pid"] == 101 and rec["status"] == "rolled_back"
    assert not (log_dir / "sup1.pid").exists()


def test_retry_recovers_already_running_lane_without_relaunch(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "sup1.pid").write_text("202", encoding="utf-8")
    receipt = log_dir / "sup1.receipt.json"
    receipt.write_text(json.dumps({"session": "sup1", "role": "supervisor", "pid": 202, "status": "active"}))

    spawn_calls = []

    def fake_spawn(*a, **_):
        spawn_calls.append(a[1])
        a[4].write_text("203", encoding="utf-8")
        return 203

    plan = [
        {"role": "supervisor", "session": "sup1", "prompt": "p1"},
        {"role": "lane", "session": "lane2", "prompt": "p2"},
    ]
    with (
        patch.dict(os.environ, {"RUNNER_DASHBOARD_ROOT": str(_ROOT)}),
        patch.object(spawn, "_spawn_one", side_effect=fake_spawn),
        patch.object(spawn, "_verify_process_identity", side_effect=lambda p, s: p == 202 and s == "sup1"),
        patch.object(spawn.sys, "stdin", io.StringIO(json.dumps(plan))),
        patch.object(spawn.sys, "stdout", io.StringIO()) as out,
    ):
        assert spawn.main([str(log_dir)]) == 0
        res = json.loads(out.getvalue())
        assert res["supervisor"] == 202 and res["lane1"] == 203

    assert spawn_calls == ["lane2"]
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "recovered"


def test_cleanup_never_targets_unrelated_pids() -> None:
    with patch("os.kill") as mock_kill:
        spawn._rollback_launched(Path("/nonexistent"), [("s0", 0), ("s1", 1), ("sneg", -5)])
        mock_kill.assert_not_called()


def test_partial_cleanup_failure_retains_recovery_evidence(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    pidfile = log_dir / "lane1.pid"
    pidfile.write_text("303", encoding="utf-8")
    (log_dir / "lane1.receipt.json").write_text(
        json.dumps({"session": "lane1", "role": "lane", "pid": 303, "status": "active"})
    )

    with patch("os.kill", side_effect=lambda pid, sig: None if sig == 0 else None):
        spawn._rollback_launched(log_dir, [("lane1", 303)])

    assert pidfile.is_file() and pidfile.read_text().strip() == "303"
    rec = json.loads((log_dir / "lane1.receipt.json").read_text())
    assert rec["status"] == "termination_pending"
    ev = json.loads((log_dir / "recovery_evidence.json").read_text())
    assert ev["cleanup_failures"]["lane1"]["status"] == "termination_pending"


def test_receipt_write_failure_after_spawn_does_not_orphan_worker(tmp_path: Path) -> None:
    """Adversarial Case 1: Receipt write failure must roll back newly spawned worker, not orphan it."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    alive, kill_log = {505: True}, []

    def fake_spawn(*a, **_):
        a[4].write_text("505", encoding="utf-8")
        return 505

    plan = [{"role": "supervisor", "session": "sup-orphanguard", "prompt": "p"}]
    with (
        patch.dict(os.environ, {"RUNNER_DASHBOARD_ROOT": str(_ROOT)}),
        patch.object(spawn, "_spawn_one", side_effect=fake_spawn),
        patch.object(spawn, "_write_lane_receipt", side_effect=OSError("disk full")),
        patch("os.kill", side_effect=_make_kill(alive, kill_log)),
        patch.object(spawn.sys, "stdin", io.StringIO(json.dumps(plan))),
    ):
        with pytest.raises(OSError, match="disk full"):
            spawn.main([str(log_dir)])

    assert (505, signal.SIGTERM) in kill_log and not alive[505]


def test_pid_reuse_rejection_prevents_adopting_unrelated_process(tmp_path: Path) -> None:
    """Adversarial Case 2: Process adoption must verify identity and reject unrelated reused PIDs."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "sup-reuse.pid").write_text("606", encoding="utf-8")
    spawned = []

    def fake_spawn(*a, **_):
        spawned.append(a[1])
        a[4].write_text("707", encoding="utf-8")
        return 707

    def fake_cmd(pid: int):
        return ["/usr/sbin/sshd", "-D"] if pid == 606 else ["bash", "lane.sh", "sup-reuse"]

    def fake_kill(pid: int, sig: int):
        if sig == 0 and pid in (606, 707):
            return
        raise ProcessLookupError

    plan = [{"role": "supervisor", "session": "sup-reuse", "prompt": "p"}]
    with (
        patch.dict(os.environ, {"RUNNER_DASHBOARD_ROOT": str(_ROOT)}),
        patch.object(spawn, "_spawn_one", side_effect=fake_spawn),
        patch.object(spawn, "_get_process_cmdline", side_effect=fake_cmd),
        patch("os.kill", side_effect=fake_kill),
        patch.object(spawn.sys, "stdin", io.StringIO(json.dumps(plan))),
        patch.object(spawn.sys, "stdout", io.StringIO()) as out,
    ):
        assert spawn.main([str(log_dir)]) == 0
        assert json.loads(out.getvalue())["supervisor"] == 707
        assert spawned == ["sup-reuse"]


def test_rollback_preserves_recovered_workers_on_subsequent_failure(tmp_path: Path) -> None:
    """Adversarial Case 3: Rollback on partial failure must ONLY roll back newly created processes."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "sup-rec.pid").write_text("808", encoding="utf-8")
    (log_dir / "sup-rec.receipt.json").write_text(
        json.dumps({"session": "sup-rec", "role": "supervisor", "pid": 808, "status": "active"})
    )
    alive, kill_log = {808: True, 909: True}, []

    def fake_spawn(*a, **_):
        if a[1] == "lane2":
            a[4].write_text("909", encoding="utf-8")
            return 909
        raise RuntimeError("lane3 boom")

    plan = [
        {"role": "supervisor", "session": "sup-rec", "prompt": "p1"},
        {"role": "lane", "session": "lane2", "prompt": "p2"},
        {"role": "lane", "session": "lane3", "prompt": "p3"},
    ]
    with (
        patch.dict(os.environ, {"RUNNER_DASHBOARD_ROOT": str(_ROOT)}),
        patch.object(spawn, "_spawn_one", side_effect=fake_spawn),
        patch.object(spawn, "_verify_process_identity", side_effect=lambda p, s: p == 808 and s == "sup-rec"),
        patch("os.kill", side_effect=_make_kill(alive, kill_log)),
        patch.object(spawn.sys, "stdin", io.StringIO(json.dumps(plan))),
    ):
        with pytest.raises(RuntimeError, match="lane3 boom"):
            spawn.main([str(log_dir)])

    assert 808 not in [p for p, s in kill_log if s != 0] and alive[808]
    assert (909, signal.SIGTERM) in kill_log and not alive[909]
    assert (log_dir / "sup-rec.pid").is_file() and not (log_dir / "lane2.pid").exists()


def test_rollback_bounded_wait_and_sigkill_escalation(tmp_path: Path) -> None:
    """Adversarial Case 4: SIGTERM must poll for exit; if alive, escalate to SIGKILL."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "stuck.pid").write_text("1111", encoding="utf-8")
    (log_dir / "stuck.receipt.json").write_text(
        json.dumps({"session": "stuck", "role": "lane", "pid": 1111, "status": "active"})
    )
    calls, alive = [], {1111: True}

    def stubborn_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))
        if sig == 0 and not alive[pid]:
            raise ProcessLookupError
        elif sig == _SIGKILL:
            alive[pid] = False

    with patch("os.kill", side_effect=stubborn_kill):
        spawn._rollback_launched(log_dir, [("stuck", 1111)])

    sigs = [sig for p, sig in calls if p == 1111 and sig != 0]
    assert signal.SIGTERM in sigs and _SIGKILL in sigs
    rec = json.loads((log_dir / "stuck.receipt.json").read_text(encoding="utf-8"))
    assert rec["status"] == "rolled_back" and not (log_dir / "stuck.pid").exists()


def test_failed_receipt_persistence_retains_recovery_evidence(tmp_path: Path) -> None:
    """Adversarial Case 5: Receipt persistence failure must retain reconstructible evidence."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    with (
        patch("pathlib.Path.write_text", side_effect=OSError("io err")),
        patch.object(spawn, "_record_recovery_evidence") as mock_rec,
    ):
        with pytest.raises(OSError, match="io err"):
            spawn._write_lane_receipt(log_dir, "lane-io", "lane", 1212)
        mock_rec.assert_called_once()
        assert mock_rec.call_args[0][2]["pid"] == 1212

    spawn._record_recovery_evidence(log_dir, "rec_test", {"pid": 1313, "status": "recovered"})
    ev = json.loads((log_dir / "recovery_evidence.json").read_text(encoding="utf-8"))
    assert ev["records"]["rec_test"]["pid"] == 1313
