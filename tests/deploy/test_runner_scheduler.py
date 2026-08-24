"""Tests for deploy/runner-scheduler.py capacity planning and provenance."""

from __future__ import annotations

import importlib.util
import os
import sys
import time
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
