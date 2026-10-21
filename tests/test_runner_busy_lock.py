"""Behavioural tests for the runner-busy lock staleness rule (#1987).

Same four cases as tests/deploy/test_runner_busy_lock_gc.py (bash), driven
against a fake proc root, for every Python consumer of the rule:
backend/runner_busy_lock.py, autoscaler_busy._runner_busy_via_lockfile and
deploy/runner-scheduler.py runner_busy_via_lock.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import autoscaler_busy as busy
import pytest
import runner_busy_lock as rbl

ROOT = Path(__file__).resolve().parents[1]

WORKER_CMDLINE = "/home/runner/actions-runners/runner-1/bin/Runner.Worker\0spawnclient\0123\0456\0"

# (lock body, fake procs {pid: cmdline}, expected busy / lock kept)
CASES = {
    "live_worker_kept": ("pid=11\nworker_pid=100\nrunner=runner-1\n", {100: WORKER_CMDLINE}, True),
    "dead_worker_pid_removed": ("pid=11\nworker_pid=100\nrunner=runner-1\n", {}, False),
    "reused_pid_removed": ("pid=11\nworker_pid=100\nrunner=runner-1\n", {100: "/usr/bin/python3\0-m\0x\0"}, False),
    "legacy_dead_hook_pid_kept": ("pid=11\nrunner=runner-1\n", {}, True),
}


def _fake_proc(root: Path, procs: dict[int, str]) -> Path:
    proc = root / "proc"
    proc.mkdir()
    for pid, cmdline in procs.items():
        (proc / str(pid)).mkdir()
        (proc / str(pid) / "cmdline").write_bytes(cmdline.encode())
    return proc


@pytest.mark.parametrize("case", sorted(CASES))
def test_lock_is_stale_by_worker_pid(tmp_path: Path, case: str) -> None:
    body, procs, alive = CASES[case]
    proc = _fake_proc(tmp_path, procs)
    assert rbl.lock_is_stale_by_worker_pid(body, proc) is (not alive)


def test_proc_root_defaults_to_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROC_ROOT", str(_fake_proc(tmp_path, {})))
    assert rbl.lock_is_stale_by_worker_pid("worker_pid=100\n") is True


def test_missing_proc_root_is_never_stale(tmp_path: Path) -> None:
    assert rbl.lock_is_stale_by_worker_pid("worker_pid=100\n", tmp_path / "no-proc") is False


@pytest.mark.parametrize("body", ["worker_pid=\n", "worker_pid=abc\n", "worker_pid=0\n", ""])
def test_malformed_or_absent_worker_pid_is_never_stale(tmp_path: Path, body: str) -> None:
    assert rbl.lock_is_stale_by_worker_pid(body, _fake_proc(tmp_path, {})) is False


# --- autoscaler_busy ---------------------------------------------------------

UNIT = "actions.runner.D-sorganization.runner-1.service"


@pytest.mark.parametrize("case", sorted(CASES))
def test_autoscaler_lockfile_signal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    body, procs, busy_expected = CASES[case]
    locks = tmp_path / "locks"
    locks.mkdir()
    lock = locks / "runner-1.lock"
    lock.write_text(body, encoding="utf-8")
    monkeypatch.setattr(busy, "RUNNER_BUSY_LOCK_DIR", locks)
    monkeypatch.setenv("PROC_ROOT", str(_fake_proc(tmp_path, procs)))
    # The Worker scan must not rescue or condemn the lock: the rule is pid-based.
    monkeypatch.setattr(busy, "_runner_busy_via_worker_scan_public", lambda _u: False)

    assert busy._runner_busy_via_lockfile(UNIT) is busy_expected
    assert lock.exists(), "the autoscaler never deletes locks; runner-cleanup.sh does"


# --- deploy/runner-scheduler.py ---------------------------------------------


def _load_scheduler():
    spec = importlib.util.spec_from_file_location("runner_scheduler_lockgc", ROOT / "deploy" / "runner-scheduler.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("case", sorted(CASES))
@pytest.mark.parametrize(
    "cleanup_stale",
    [
        pytest.param(None, id="default-read-only"),
        pytest.param(False, id="explicit-read-only"),
        pytest.param(True, id="explicit-cleanup"),
    ],
)
def test_scheduler_lock_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    cleanup_stale: bool | None,
) -> None:
    sched = _load_scheduler()
    body, procs, busy_expected = CASES[case]
    workdir = tmp_path / "runner-1"
    workdir.mkdir()
    (workdir / ".runner").write_text(json.dumps({"agentName": "runner-1"}), encoding="utf-8")
    locks = tmp_path / "locks"
    locks.mkdir()
    lock = locks / "runner-1.lock"
    lock.write_text(body, encoding="utf-8")
    monkeypatch.setattr(sched, "RUNNER_BUSY_LOCK_DIR", locks)
    monkeypatch.setattr(sched, "PROC_ROOT", _fake_proc(tmp_path, procs))
    monkeypatch.setattr(sched, "runner_busy_via_process", lambda _w: False)

    if cleanup_stale is None:
        busy = sched.runner_busy_via_lock(workdir)
    else:
        busy = sched.runner_busy_via_lock(workdir, cleanup_stale=cleanup_stale)

    assert busy is busy_expected
    expected_lock_exists = busy_expected if cleanup_stale is True else True
    assert lock.exists() is expected_lock_exists
