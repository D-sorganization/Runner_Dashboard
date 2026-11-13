"""Regression tests for #4152: candidate-pid identity and rejected-candidate evidence.

#4151's _verify_process_identity only checks the *parent's* invocation shape.
It never confirms the recorded pid itself is a plausible worker process, so a
pid reused by an unrelated process under a still-matching long-lived parent
would be adopted as recovered. Separately, a previously-recorded candidate
that fails verification had its pid/receipt silently discarded once the new
worker's receipt overwrote it.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location("bpush_spawn_4152", _ROOT / "scripts/barb-worker/backlog-push-spawn.py")
assert _spec and _spec.loader
spawn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spawn)

_PID = 606
_PPID = 500
_VALID_PARENT_CMDLINE = [
    "/bin/bash",
    "/opt/Runner_Dashboard/scripts/barb-worker/backlog-push-lane.sh",
    "sup-reuse",
    "/tmp/x.log",
    "/tmp/x.prompt",
    "/tmp/x.pid",
]


def _alive(pid: int):
    return patch("os.kill", side_effect=lambda p, sig: None if p == pid else (_ for _ in ()).throw(ProcessLookupError))


def _with_parent_and_own(parent_cmdline, own_cmdline):
    """Patch ppid lookup plus cmdline lookup for both the parent and the candidate itself."""
    by_pid = {_PID: own_cmdline, _PPID: parent_cmdline}
    ppid_patch = patch.object(spawn, "_get_process_ppid", return_value=_PPID)
    cmdline_patch = patch.object(spawn, "_get_process_cmdline", side_effect=by_pid.get)
    return ppid_patch, cmdline_patch


def test_unrelated_own_process_rejected_despite_matching_parent() -> None:
    """A reused pid under a still-matching parent, but not itself a worker, must be rejected."""
    ppid_patch, cmdline_patch = _with_parent_and_own(_VALID_PARENT_CMDLINE, ["/usr/bin/sleep", "3600"])
    with _alive(_PID), ppid_patch, cmdline_patch:
        assert spawn._verify_process_identity(_PID, "sup-reuse") is True
        assert spawn._candidate_matches_worker_shape(_PID) is False
        assert spawn._recoverable_pid(_PID, "sup-reuse") is False


def test_real_worker_own_process_matches_with_valid_parent() -> None:
    """The actual worker's own argv[0] is `claude`; it is recoverable."""
    own_cmdline = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    ppid_patch, cmdline_patch = _with_parent_and_own(_VALID_PARENT_CMDLINE, own_cmdline)
    with _alive(_PID), ppid_patch, cmdline_patch:
        assert spawn._candidate_matches_worker_shape(_PID) is True
        assert spawn._recoverable_pid(_PID, "sup-reuse") is True


def test_candidate_matches_worker_shape_missing_cmdline_rejected() -> None:
    with patch.object(spawn, "_get_process_cmdline", return_value=None):
        assert spawn._candidate_matches_worker_shape(_PID) is False


def test_rejected_candidate_evidence_retained_on_relaunch(tmp_path: Path) -> None:
    """A rejected candidate's prior pid/receipt must survive in recovery_evidence.json
    even after a newly launched worker overwrites the session's receipt file."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    session = "sup-reuse"

    # Simulate a prior run's receipt for a now-stale/rejected pid.
    prior_receipt_path = log_dir / f"{session}.receipt.json"
    prior_receipt_path.write_text(
        json.dumps({"session": session, "role": "supervisor", "pid": _PID, "status": "recovered"}),
        encoding="utf-8",
    )
    pidfile = log_dir / f"{session}.pid"
    pidfile.write_text(str(_PID), encoding="utf-8")

    plan = [{"role": "supervisor", "session": session, "prompt": "p1"}]

    def fake_spawn(_lane_sh, _session, _log_path, _prompt_path, _pidfile, _env):
        _pidfile.write_text("777", encoding="utf-8")
        return 777

    with (
        patch.dict(os.environ, {"RUNNER_DASHBOARD_ROOT": str(_ROOT)}),
        patch.object(spawn, "_recoverable_pid", return_value=False),
        patch.object(spawn, "_spawn_one", side_effect=fake_spawn),
        patch.object(spawn.sys, "stdin", io.StringIO(json.dumps(plan))),
    ):
        out = spawn.main([str(log_dir)])

    assert out == 0

    new_receipt = json.loads(prior_receipt_path.read_text(encoding="utf-8"))
    assert new_receipt["pid"] == 777
    assert new_receipt["status"] == "active"

    evidence = json.loads((log_dir / "recovery_evidence.json").read_text(encoding="utf-8"))
    rejected = evidence["records"][f"rejected_candidate_{session}"]
    assert rejected["rejected_pid"] == _PID
    assert rejected["prior_receipt"]["pid"] == _PID
    assert rejected["prior_receipt"]["status"] == "recovered"
