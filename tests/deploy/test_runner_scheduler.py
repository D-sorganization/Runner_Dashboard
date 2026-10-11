"""Tests for deploy/runner-scheduler.py capacity planning and provenance."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULER_PATH = REPO_ROOT / "deploy" / "runner-scheduler.py"

spec = importlib.util.spec_from_file_location("runner_scheduler", SCHEDULER_PATH)
assert spec and spec.loader
runner_scheduler = importlib.util.module_from_spec(spec)
sys.modules["runner_scheduler"] = runner_scheduler
spec.loader.exec_module(runner_scheduler)


def test_desired_capacity_with_manual_target_provenance() -> None:
    config = {"enabled": True, "default_count": 4, "schedules": []}
    desired, reason = runner_scheduler.desired_capacity(config, target_override=1, target_source="cli: --target")
    assert desired == 1
    assert reason == "manual-target (cli: --target)"


def test_desired_capacity_with_manual_target_without_source() -> None:
    config = {"enabled": True, "default_count": 4, "schedules": []}
    desired, reason = runner_scheduler.desired_capacity(config, target_override=2)
    assert desired == 2
    assert reason == "manual-target"


def test_desired_capacity_schedule_disabled() -> None:
    config = {"enabled": False, "default_count": 3, "schedules": []}
    desired, reason = runner_scheduler.desired_capacity(config)
    assert desired == 3
    assert reason == "schedule-disabled"


def test_desired_capacity_default_fallback() -> None:
    config = {"enabled": True, "default_count": 5, "schedules": []}
    desired, reason = runner_scheduler.desired_capacity(config)
    assert desired == 5
    assert reason == "default"


def test_canonical_schedule_covers_daytime_and_overnight_windows() -> None:
    config = json.loads((REPO_ROOT / "config" / "runner-schedule.json").read_text(encoding="utf-8"))
    entries = {entry["name"]: entry for entry in config["schedules"]}

    assert runner_scheduler.schedule_matches(entries["weekday-day"], datetime(2026, 8, 25, 12, 0))
    assert runner_scheduler.schedule_matches(entries["weekend-day"], datetime(2026, 8, 29, 12, 0))
    assert runner_scheduler.schedule_matches(entries["overnight"], datetime(2026, 8, 25, 23, 0))
    assert runner_scheduler.schedule_matches(entries["overnight"], datetime(2026, 8, 26, 6, 0))


def _process_result(stdout: str = "", returncode: int = 0):
    return runner_scheduler.subprocess.CompletedProcess(args=["ps"], returncode=returncode, stdout=stdout, stderr="")


def test_runner_busy_detects_versioned_worker(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path / "runner-5"
    worker = workdir / "bin.2.336.0" / "Runner.Worker"
    monkeypatch.setattr(
        runner_scheduler,
        "run_cmd",
        lambda *_args, **_kwargs: _process_result(f"{worker} spawnclient 157 161\n"),
    )

    assert runner_scheduler.runner_busy(workdir) is True


def test_runner_busy_detects_legacy_worker(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path / "runner-5"
    worker = workdir / "bin" / "Runner.Worker"
    monkeypatch.setattr(
        runner_scheduler,
        "run_cmd",
        lambda *_args, **_kwargs: _process_result(f"{worker} spawnclient 157 161\n"),
    )

    assert runner_scheduler.runner_busy(workdir) is True


def test_runner_busy_does_not_match_another_runner(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path / "runner-5"
    other_worker = tmp_path / "runner-50" / "bin.2.336.0" / "Runner.Worker"
    monkeypatch.setattr(
        runner_scheduler,
        "run_cmd",
        lambda *_args, **_kwargs: _process_result(f"{other_worker} spawnclient\n"),
    )

    assert runner_scheduler.runner_busy(workdir) is False


def test_runner_busy_fails_closed_when_process_inventory_fails(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        runner_scheduler,
        "run_cmd",
        lambda *_args, **_kwargs: _process_result(returncode=2),
    )

    assert runner_scheduler.runner_busy(tmp_path / "runner-5") is True


def test_runner_busy_detects_recent_pickup_before_worker_forks(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path / "runner-5"
    pickup = workdir / "_work" / "_temp" / "_runner_file_commands"
    pickup.mkdir(parents=True)
    monkeypatch.setattr(
        runner_scheduler,
        "run_cmd",
        lambda *_args, **_kwargs: _process_result(),
    )

    assert runner_scheduler.runner_busy(workdir) is True


def test_runner_busy_ignores_stale_pickup(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path / "runner-5"
    pickup = workdir / "_work" / "_temp" / "_runner_file_commands"
    pickup.mkdir(parents=True)
    stale = time.time() - runner_scheduler.RUNNER_PICKUP_MAX_AGE_SECONDS - 1
    os.utime(pickup, (stale, stale))
    monkeypatch.setattr(
        runner_scheduler,
        "run_cmd",
        lambda *_args, **_kwargs: _process_result(),
    )

    assert runner_scheduler.runner_busy(workdir) is False


def test_runner_busy_fails_closed_when_pickup_metadata_is_unreadable(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path / "runner-5"
    pickup = workdir / "_work" / "_temp" / "_runner_file_commands"
    pickup.mkdir(parents=True)
    original_stat = Path.stat

    def unreadable_pickup(path: Path, *args, **kwargs):
        if path == pickup:
            raise PermissionError("denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", unreadable_pickup)

    assert runner_scheduler.runner_busy(workdir) is True


def test_runner_busy_detects_fresh_hook_lock(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path / "runner-5"
    lock_dir = tmp_path / "runner-busy"
    lock_dir.mkdir()
    (lock_dir / "runner-5.lock").write_text("busy\n", encoding="utf-8")
    monkeypatch.setattr(runner_scheduler, "RUNNER_BUSY_LOCK_DIR", lock_dir)
    monkeypatch.setattr(
        runner_scheduler,
        "run_cmd",
        lambda *_args, **_kwargs: _process_result(),
    )

    assert runner_scheduler.runner_busy(workdir) is True


def test_apply_capacity_does_not_start_inactive_busy_runner(tmp_path: Path) -> None:
    units = [
        runner_scheduler.RunnerUnit(
            num=5,
            unit="actions.runner.example.runner-5.service",
            workdir=tmp_path / "runner-5",
            active=False,
            busy=True,
        )
    ]

    assert runner_scheduler.apply_capacity(units, desired=1, dry_run=True) == []


def test_desired_capacity_never_exceeds_configured_maximum() -> None:
    config = {
        "enabled": True,
        "default_count": 4,
        "max_count": 6,
        "schedules": [],
    }

    assert runner_scheduler.desired_capacity(config, target_override=8) == (
        6,
        "manual-target (capped at max_count=6)",
    )


# --- Issue #1921: scheduler must never stop a busy / just-assigned runner -----


def _no_ps(monkeypatch) -> None:
    monkeypatch.setattr(runner_scheduler, "run_cmd", lambda *_a, **_k: _process_result())
    monkeypatch.setattr(runner_scheduler, "_proc_cmdlines", lambda: [])


def test_runner_busy_lock_keyed_on_registered_runner_name(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path / "runner-3"
    workdir.mkdir()
    # .runner is written with a UTF-8 BOM by the Actions runner.
    (workdir / ".runner").write_text(json.dumps({"agentName": "d-sorg-local-Desktop-3"}), encoding="utf-8-sig")
    lock_dir = tmp_path / "runner-busy"
    lock_dir.mkdir()
    (lock_dir / "d-sorg-local-Desktop-3.lock").write_text("busy\n", encoding="utf-8")
    monkeypatch.setattr(runner_scheduler, "RUNNER_BUSY_LOCK_DIR", lock_dir)
    _no_ps(monkeypatch)

    assert runner_scheduler.runner_busy(workdir) is True


def test_runner_busy_lock_keyed_on_unit_name_when_runner_file_missing(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path / "runner-3"
    workdir.mkdir()
    lock_dir = tmp_path / "runner-busy"
    lock_dir.mkdir()
    (lock_dir / "d-sorg-local-Desktop-3.lock").write_text("busy\n", encoding="utf-8")
    monkeypatch.setattr(runner_scheduler, "RUNNER_BUSY_LOCK_DIR", lock_dir)
    _no_ps(monkeypatch)

    unit = "actions.runner.D-sorganization.d-sorg-local-Desktop-3.service"
    assert runner_scheduler.runner_busy(workdir, unit=unit) is True


def test_runner_busy_lock_for_other_runner_is_ignored(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path / "runner-3"
    workdir.mkdir()
    (workdir / ".runner").write_text(json.dumps({"agentName": "d-sorg-local-Desktop-3"}), encoding="utf-8")
    lock_dir = tmp_path / "runner-busy"
    lock_dir.mkdir()
    (lock_dir / "d-sorg-local-Desktop-1.lock").write_text("busy\n", encoding="utf-8")
    monkeypatch.setattr(runner_scheduler, "RUNNER_BUSY_LOCK_DIR", lock_dir)
    _no_ps(monkeypatch)

    assert runner_scheduler.runner_busy(workdir) is False


def test_runner_busy_detects_worker_via_proc_when_ps_misses_it(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path / "runner-3"
    worker = workdir / "bin.2.337.0" / "Runner.Worker"
    monkeypatch.setattr(runner_scheduler, "run_cmd", lambda *_a, **_k: _process_result())
    monkeypatch.setattr(runner_scheduler, "_proc_cmdlines", lambda: [f"{worker} spawnclient 154 160"])

    assert runner_scheduler.runner_busy(workdir) is True


def test_proc_cmdlines_reads_nul_separated_cmdline(monkeypatch, tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    (proc / "123").mkdir(parents=True)
    (proc / "123" / "cmdline").write_bytes(b"/x/runner-3/bin/Runner.Worker\x00spawnclient\x001\x002\x00")
    (proc / "self").mkdir()
    (proc / "notapid").mkdir()
    monkeypatch.setattr(runner_scheduler, "PROC_ROOT", proc)

    assert runner_scheduler._proc_cmdlines() == ["/x/runner-3/bin/Runner.Worker spawnclient 1 2"]


def test_runner_busy_detects_recent_diag_activity(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path / "runner-3"
    diag = workdir / "_diag"
    diag.mkdir(parents=True)
    (diag / "Worker_20261011-010415-utc.log").write_text("x", encoding="utf-8")
    _no_ps(monkeypatch)

    assert runner_scheduler.runner_busy(workdir) is True


def test_runner_busy_ignores_stale_diag_activity(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path / "runner-3"
    diag = workdir / "_diag"
    diag.mkdir(parents=True)
    log = diag / "Worker_20261011-010415-utc.log"
    log.write_text("x", encoding="utf-8")
    stale = time.time() - runner_scheduler.RUNNER_DIAG_MAX_AGE_SECONDS - 5
    os.utime(log, (stale, stale))
    _no_ps(monkeypatch)

    assert runner_scheduler.runner_busy(workdir) is False


def _active_unit(num: int, tmp_path: Path, busy: bool = False):
    return runner_scheduler.RunnerUnit(
        num=num,
        unit=f"actions.runner.example.runner-{num}.service",
        workdir=tmp_path / f"runner-{num}",
        active=True,
        busy=busy,
    )


def test_apply_capacity_rechecks_busy_immediately_before_stop(monkeypatch, tmp_path: Path) -> None:
    """A runner that picked up a job after the snapshot must not be stopped (TOCTOU)."""
    commands: list[list[str]] = []
    monkeypatch.setattr(runner_scheduler, "run_cmd", lambda cmd, **_k: commands.append(cmd) or _process_result())
    monkeypatch.setattr(runner_scheduler, "STOP_RECHECK_DELAY_SECONDS", 0)
    monkeypatch.setattr(runner_scheduler, "runner_busy", lambda *_a, **_k: True)
    units = [_active_unit(1, tmp_path), _active_unit(2, tmp_path)]

    actions = runner_scheduler.apply_capacity(units, desired=1, dry_run=False)

    assert actions == []
    assert not any(cmd[:2] == ["systemctl", "stop"] for cmd in commands)


def test_apply_capacity_stops_runner_that_stays_idle(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(runner_scheduler, "run_cmd", lambda cmd, **_k: commands.append(cmd) or _process_result())
    monkeypatch.setattr(runner_scheduler, "STOP_RECHECK_DELAY_SECONDS", 0)
    monkeypatch.setattr(runner_scheduler, "runner_busy", lambda *_a, **_k: False)
    units = [_active_unit(1, tmp_path), _active_unit(2, tmp_path)]

    actions = runner_scheduler.apply_capacity(units, desired=1, dry_run=False)

    assert [a["runner"] for a in actions] == [2]
    assert ["systemctl", "stop", units[1].unit] in commands


def test_apply_capacity_falls_back_to_next_idle_runner_when_first_candidate_turns_busy(
    monkeypatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(runner_scheduler, "run_cmd", lambda cmd, **_k: commands.append(cmd) or _process_result())
    monkeypatch.setattr(runner_scheduler, "STOP_RECHECK_DELAY_SECONDS", 0)
    monkeypatch.setattr(runner_scheduler, "runner_busy", lambda workdir, **_k: workdir.name == "runner-3")
    units = [_active_unit(n, tmp_path) for n in (1, 2, 3)]

    actions = runner_scheduler.apply_capacity(units, desired=2, dry_run=False)

    assert [a["runner"] for a in actions] == [2]
