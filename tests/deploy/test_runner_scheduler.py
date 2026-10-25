"""Tests for deploy/runner-scheduler.py capacity planning and provenance."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("worker_state", ["dead", "reused"])
def test_preview_lock_inspection_preserves_stale_lock_bytes(monkeypatch, tmp_path: Path, worker_state: str) -> None:
    workdir = tmp_path / "runner-5"
    workdir.mkdir()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    if worker_state == "reused":
        pid_dir = proc_root / "4242"
        pid_dir.mkdir()
        (pid_dir / "cmdline").write_bytes(b"python\x00other")

    lock_dir = tmp_path / "runner-busy"
    lock_dir.mkdir()
    lock = lock_dir / "runner-5.lock"
    original = b"pid=101\nworker_pid=4242\n"
    lock.write_bytes(original)
    monkeypatch.setattr(runner_scheduler, "RUNNER_BUSY_LOCK_DIR", lock_dir)
    monkeypatch.setattr(runner_scheduler, "PROC_ROOT", proc_root)

    assert runner_scheduler.runner_busy_via_lock(workdir, cleanup_stale=False) is False
    assert runner_scheduler.runner_busy_via_lock(workdir, cleanup_stale=False) is False
    assert lock.read_bytes() == original


@pytest.mark.parametrize("worker_state", ["dead", "reused"])
def test_applied_lock_inspection_removes_stale_lock(monkeypatch, tmp_path: Path, worker_state: str) -> None:
    workdir = tmp_path / "runner-5"
    workdir.mkdir()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    if worker_state == "reused":
        pid_dir = proc_root / "4242"
        pid_dir.mkdir()
        (pid_dir / "cmdline").write_bytes(b"python\x00other")
    lock_dir = tmp_path / "runner-busy"
    lock_dir.mkdir()
    lock = lock_dir / "runner-5.lock"
    lock.write_bytes(b"pid=101\nworker_pid=4242\n")
    monkeypatch.setattr(runner_scheduler, "RUNNER_BUSY_LOCK_DIR", lock_dir)
    monkeypatch.setattr(runner_scheduler, "PROC_ROOT", proc_root)

    assert runner_scheduler.runner_busy_via_lock(workdir, cleanup_stale=True) is False
    assert not lock.exists()


@pytest.mark.parametrize("worker_state", ["dead", "reused"])
def test_main_dry_run_preserves_stale_lock_during_scale_down(
    monkeypatch, tmp_path: Path, capsys, worker_state: str
) -> None:
    runner_root = tmp_path / "runners"
    runner_root.mkdir()
    for number in (1, 2):
        (runner_root / f"runner-{number}").mkdir()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    if worker_state == "reused":
        pid_dir = proc_root / "4242"
        pid_dir.mkdir()
        (pid_dir / "cmdline").write_bytes(b"python\x00other")
    lock_dir = tmp_path / "runner-busy"
    lock_dir.mkdir()
    lock = lock_dir / "runner-2.lock"
    original = b"pid=101\nworker_pid=4242\n"
    lock.write_bytes(original)
    monkeypatch.setattr(runner_scheduler, "RUNNER_ROOT", runner_root)
    monkeypatch.setattr(runner_scheduler, "RUNNER_BUSY_LOCK_DIR", lock_dir)
    monkeypatch.setattr(runner_scheduler, "PROC_ROOT", proc_root)
    monkeypatch.setattr(runner_scheduler, "MAINTENANCE_PATH", tmp_path / "maintenance.json")
    monkeypatch.setattr(runner_scheduler, "load_config", lambda: {"enabled": True, "default_count": 1, "schedules": []})
    monkeypatch.setattr(sys, "argv", [str(SCHEDULER_PATH), "--dry-run", "--json"])

    def fake_run(command, **_kwargs):
        if command[:3] == ["systemctl", "list-unit-files", "--type=service"]:
            return _process_result(
                stdout=(
                    "actions.runner.example.runner-1.service enabled\nactions.runner.example.runner-2.service enabled\n"
                )
            )
        if command[:2] == ["systemctl", "show"]:
            runner_name = command[2].removesuffix(".service").rsplit(".", 1)[-1]
            return _process_result(stdout=str(runner_root / runner_name) + "\n")
        if command[:2] == ["systemctl", "is-active"]:
            return _process_result(returncode=0)
        if command[:2] == ["ps", "-eo"]:
            return _process_result(stdout="")
        raise AssertionError(f"unexpected command: {command!r}")

    monkeypatch.setattr(runner_scheduler, "run_cmd", fake_run)

    assert runner_scheduler.main() == 0
    state = json.loads(capsys.readouterr().out)
    assert [action["runner"] for action in state["actions"] if action["action"] == "stop"] == [2]
    assert lock.read_bytes() == original


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


# --- Drain watcher (#1929): scale down under sustained load ------------------


class _Clock:
    """Fake monotonic clock; ``sleep`` advances it so the drain loop terminates."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _drain_env(monkeypatch, tmp_path: Path, worker_running):
    """Wire the drain loop to a fake clock, recorded commands and a worker oracle.

    ``worker_running(runner_num, now)`` says whether that runner has a live job.
    """
    clock = _Clock()
    commands: list[list[str]] = []
    monkeypatch.setattr(runner_scheduler.time_module, "sleep", clock.sleep)
    monkeypatch.setattr(runner_scheduler.time_module, "monotonic", clock.monotonic)
    monkeypatch.setattr(runner_scheduler, "run_cmd", lambda cmd, **_k: commands.append(cmd) or _process_result())

    def busy(workdir: Path, unit: str | None = None) -> bool:
        return worker_running(int(workdir.name.split("-")[-1]), clock.now)

    monkeypatch.setattr(runner_scheduler, "runner_busy_via_process", lambda workdir: busy(workdir))
    monkeypatch.setattr(runner_scheduler, "runner_busy_via_lock", lambda workdir, unit=None, **_kwargs: False)
    return clock, commands


def _stops(commands: list[list[str]]) -> list[str]:
    return [cmd[2] for cmd in commands if cmd[:2] == ["systemctl", "stop"]]


def test_drain_stops_runner_that_is_between_jobs_even_when_tick_saw_all_busy(monkeypatch, tmp_path: Path) -> None:
    """The #1929 race: every runner is busy at the tick, but one goes idle and stays idle."""
    units = [_active_unit(n, tmp_path, busy=True) for n in (1, 2, 3, 4)]
    # Runner 4 finishes its job at t=20 and nothing is queued for it; the others never gap.
    clock, commands = _drain_env(monkeypatch, tmp_path, lambda num, now: not (num == 4 and now >= 20))

    actions = runner_scheduler.drain_surplus(units, desired=3, dry_run=False)

    assert [a["runner"] for a in actions] == [4]
    assert actions[0]["action"] == "stop"
    assert _stops(commands) == [units[3].unit]
    assert clock.now >= 20 + runner_scheduler.DRAIN_SETTLE_SECONDS, "stopped before the settle window elapsed"


def test_drain_never_stops_a_runner_in_a_short_gap_between_jobs(monkeypatch, tmp_path: Path) -> None:
    """#1973: a just-freed runner under a deep queue is handed the next job within seconds.

    GitHub assigns that job server-side to the listener's open session, so a stop in
    the gap orphans it (zero steps, failed after 10 min) even with the SIGSTOP freeze.
    """
    units = [_active_unit(n, tmp_path, busy=True) for n in (1, 2)]
    # Runner 2 gaps for 5 s every 60 s: a deep queue keeps feeding it.
    _clock, commands = _drain_env(monkeypatch, tmp_path, lambda num, now: not (num == 2 and now % 60 < 5))

    actions = runner_scheduler.drain_surplus(units, desired=1, dry_run=False)

    assert actions == []
    assert _stops(commands) == []


def test_drain_settle_window_restarts_when_a_job_arrives(monkeypatch, tmp_path: Path) -> None:
    units = [_active_unit(n, tmp_path, busy=True) for n in (1, 2)]
    settle = runner_scheduler.DRAIN_SETTLE_SECONDS
    # Idle from t=10, a job runs over [10 + settle/2, 10 + settle/2 + 4), then idle for good.
    job_start = 10 + settle / 2
    clock, commands = _drain_env(
        monkeypatch,
        tmp_path,
        lambda num, now: num == 1 or now < 10 or job_start <= now < job_start + 4,
    )

    actions = runner_scheduler.drain_surplus(units, desired=1, dry_run=False)

    assert [a["runner"] for a in actions] == [2]
    assert clock.now >= job_start + 4 + settle


def test_drain_never_stops_a_runner_that_stays_busy(monkeypatch, tmp_path: Path) -> None:
    units = [_active_unit(n, tmp_path, busy=True) for n in (1, 2)]
    clock, commands = _drain_env(monkeypatch, tmp_path, lambda num, now: True)

    actions = runner_scheduler.drain_surplus(units, desired=0, dry_run=False)

    assert actions == []
    assert _stops(commands) == []
    assert clock.now <= runner_scheduler.DRAIN_MAX_SECONDS + runner_scheduler.DRAIN_POLL_SECONDS


def test_drain_requires_two_consecutive_idle_checks(monkeypatch, tmp_path: Path) -> None:
    """A job that appears between the two checks (listener got it, worker forked) vetoes the stop."""
    units = [_active_unit(n, tmp_path, busy=True) for n in (1, 2)]
    calls = {"n": 0}

    def flapping(num: int, now: float) -> bool:
        if num != 2:
            return True
        calls["n"] += 1
        return calls["n"] % 2 == 0  # idle on first look, busy on the confirm look

    _clock, commands = _drain_env(monkeypatch, tmp_path, flapping)

    actions = runner_scheduler.drain_surplus(units, desired=1, dry_run=False)

    assert actions == []
    assert _stops(commands) == []


def test_drain_never_goes_below_desired(monkeypatch, tmp_path: Path) -> None:
    units = [_active_unit(n, tmp_path, busy=True) for n in (1, 2, 3, 4)]
    _clock, commands = _drain_env(monkeypatch, tmp_path, lambda num, now: False)

    actions = runner_scheduler.drain_surplus(units, desired=2, dry_run=False)

    assert sorted(a["runner"] for a in actions) == [3, 4]
    assert len(_stops(commands)) == 2


def test_drain_can_reach_zero_for_maintenance(monkeypatch, tmp_path: Path) -> None:
    units = [_active_unit(n, tmp_path, busy=True) for n in (1, 2)]
    _clock, commands = _drain_env(monkeypatch, tmp_path, lambda num, now: False)

    actions = runner_scheduler.drain_surplus(units, desired=0, dry_run=False)

    assert sorted(a["runner"] for a in actions) == [1, 2]


def test_drain_is_a_noop_without_surplus_and_in_dry_run(monkeypatch, tmp_path: Path) -> None:
    units = [_active_unit(n, tmp_path) for n in (1, 2)]
    clock, commands = _drain_env(monkeypatch, tmp_path, lambda num, now: False)

    assert runner_scheduler.drain_surplus(units, desired=2, dry_run=False) == []
    assert runner_scheduler.drain_surplus(units, desired=0, dry_run=True) == []
    assert commands == []
    assert clock.now == 0


def test_drain_disabled_when_budget_is_zero(monkeypatch, tmp_path: Path) -> None:
    units = [_active_unit(n, tmp_path) for n in (1, 2)]
    _clock, commands = _drain_env(monkeypatch, tmp_path, lambda num, now: False)
    monkeypatch.setattr(runner_scheduler, "DRAIN_MAX_SECONDS", 0)

    assert runner_scheduler.drain_surplus(units, desired=0, dry_run=False) == []
    assert commands == []


def test_drain_ignores_inactive_runners(monkeypatch, tmp_path: Path) -> None:
    inactive = runner_scheduler.RunnerUnit(
        num=9, unit="actions.runner.example.runner-9.service", workdir=tmp_path / "runner-9", active=False, busy=False
    )
    units = [_active_unit(1, tmp_path), inactive]
    _clock, commands = _drain_env(monkeypatch, tmp_path, lambda num, now: False)

    assert runner_scheduler.drain_surplus(units, desired=1, dry_run=False) == []
    assert _stops(commands) == []


def test_main_drains_surplus_left_by_the_tick_pass(monkeypatch, tmp_path: Path, capsys) -> None:
    units = [_active_unit(n, tmp_path, busy=True) for n in (1, 2)]
    config = {"enabled": True, "default_count": 1, "schedules": []}
    monkeypatch.setattr(runner_scheduler, "load_config", lambda: config)
    monkeypatch.setattr(runner_scheduler, "list_units", lambda **_kwargs: units)
    monkeypatch.setattr(runner_scheduler, "apply_capacity", lambda *_a, **_k: [])
    monkeypatch.setattr(
        runner_scheduler,
        "build_state",
        lambda *_a, **_k: {
            "desired": 1,
            "reason": "x",
            "installed": 2,
            "online": 2,
            "busy": 2,
            "idle": 0,
            "offline": 0,
        },
    )
    monkeypatch.setattr(runner_scheduler, "write_state", lambda _s: None)
    drained: list[tuple] = []
    monkeypatch.setattr(
        runner_scheduler,
        "drain_surplus",
        lambda u, desired, dry_run: drained.append((len(u), desired, dry_run)) or [],
    )
    monkeypatch.setattr(sys, "argv", ["runner-scheduler", "--apply"])

    assert runner_scheduler.main() == 0
    assert drained == [(2, 1, False)]


# --- #1929 residual: a job assigned between the idle check and the stop ------


def _freezes(commands: list[list[str]]) -> list[str]:
    return [cmd[-1] for cmd in commands if cmd[:2] == ["systemctl", "kill"] and "--signal=SIGSTOP" in cmd]


def _thaws(commands: list[list[str]]) -> list[str]:
    return [cmd[-1] for cmd in commands if cmd[:2] == ["systemctl", "kill"] and "--signal=SIGCONT" in cmd]


def test_stop_never_lands_on_a_job_assigned_after_the_idle_checks(monkeypatch, tmp_path: Path) -> None:
    """Deterministic repro: idle on both drain looks, then a job lands before the stop.

    The listener must be frozen, re-verified idle, and only then stopped; a job
    that appears in the window thaws the listener and vetoes the stop.
    """
    units = [_active_unit(n, tmp_path, busy=True) for n in (1, 2)]
    looks = {"n": 0}

    def late_job(num: int, now: float) -> bool:
        if num != 2:
            return True
        looks["n"] += 1
        return looks["n"] >= 3  # looks 1 and 2 idle, the job lands right after

    clock, commands = _drain_env(monkeypatch, tmp_path, late_job)
    monkeypatch.setattr(runner_scheduler, "DRAIN_MAX_SECONDS", 3)
    # Isolate the freeze/re-check path from the settle window (#1973).
    monkeypatch.setattr(runner_scheduler, "DRAIN_SETTLE_SECONDS", 0)

    actions = runner_scheduler.drain_surplus(units, desired=1, dry_run=False)

    assert actions == []
    assert _stops(commands) == []
    assert _freezes(commands) == [units[1].unit]
    assert _thaws(commands) == [units[1].unit]


def test_stop_is_preceded_by_freeze_and_runner_idle_at_stop_time(monkeypatch, tmp_path: Path) -> None:
    """DbC: when a stop is issued the whole unit is frozen first and no job is running."""
    units = [_active_unit(n, tmp_path, busy=True) for n in (1, 2)]
    clock, commands = _drain_env(monkeypatch, tmp_path, lambda num, now: num == 1)
    stop_context: list[tuple[bool, bool]] = []
    real_run = runner_scheduler.run_cmd

    def spying(cmd, **kw):
        if cmd[:2] == ["systemctl", "stop"]:
            frozen = ["systemctl", "kill", "--kill-who=all", "--signal=SIGSTOP", cmd[2]] in commands
            stop_context.append((frozen, runner_scheduler.runner_busy_via_process(units[1].workdir)))
        return real_run(cmd, **kw)

    monkeypatch.setattr(runner_scheduler, "run_cmd", spying)

    actions = runner_scheduler.drain_surplus(units, desired=1, dry_run=False)

    assert [a["runner"] for a in actions] == [2]
    assert _stops(commands) == [units[1].unit]
    assert stop_context == [(True, False)]


def test_stop_fails_closed_when_the_listener_cannot_be_frozen(monkeypatch, tmp_path: Path) -> None:
    units = [_active_unit(n, tmp_path, busy=True) for n in (1, 2)]
    clock, commands = _drain_env(monkeypatch, tmp_path, lambda num, now: num == 1)
    monkeypatch.setattr(runner_scheduler, "DRAIN_MAX_SECONDS", 3)
    # Isolate the freeze/re-check path from the settle window (#1973).
    monkeypatch.setattr(runner_scheduler, "DRAIN_SETTLE_SECONDS", 0)

    def failing_freeze(cmd, **_k):
        commands.append(cmd)
        return _process_result(returncode=1 if "--signal=SIGSTOP" in cmd else 0)

    monkeypatch.setattr(runner_scheduler, "run_cmd", failing_freeze)

    assert runner_scheduler.drain_surplus(units, desired=1, dry_run=False) == []
    assert _stops(commands) == []


def test_unit_is_thawed_when_the_recheck_raises(monkeypatch, tmp_path: Path) -> None:
    """A frozen unit must never be left frozen if verification blows up."""
    unit = _active_unit(2, tmp_path, busy=True)
    commands: list[list[str]] = []
    monkeypatch.setattr(runner_scheduler, "run_cmd", lambda cmd, **_k: commands.append(cmd) or _process_result())

    def boom(_workdir):
        raise OSError("proc unreadable")

    monkeypatch.setattr(runner_scheduler, "runner_busy_via_process", boom)

    try:
        result = runner_scheduler.stop_runner_safely(unit)
    except OSError:
        result = False

    assert result is False
    assert _thaws(commands) == [unit.unit]
    assert _stops(commands) == []


def test_unit_is_thawed_when_systemctl_stop_fails(monkeypatch, tmp_path: Path) -> None:
    unit = _active_unit(2, tmp_path, busy=True)
    commands: list[list[str]] = []

    def run(cmd, **_k):
        commands.append(cmd)
        return _process_result(returncode=1 if cmd[:2] == ["systemctl", "stop"] else 0)

    monkeypatch.setattr(runner_scheduler, "run_cmd", run)
    monkeypatch.setattr(runner_scheduler, "runner_busy_via_process", lambda _w: False)
    monkeypatch.setattr(runner_scheduler, "runner_busy_via_lock", lambda _w, _u=None, **_kwargs: False)

    assert runner_scheduler.stop_runner_safely(unit) is False
    assert _thaws(commands) == [unit.unit]


def test_successful_stop_resumes_children_after_stop_submission(monkeypatch, tmp_path: Path) -> None:
    unit = _active_unit(2, tmp_path, busy=True)
    commands: list[list[str]] = []
    monkeypatch.setattr(runner_scheduler, "run_cmd", lambda cmd, **_k: commands.append(cmd) or _process_result())
    monkeypatch.setattr(runner_scheduler, "runner_busy_via_process", lambda _w: False)
    monkeypatch.setattr(runner_scheduler, "runner_busy_via_lock", lambda _w, _u=None, **_kwargs: False)

    assert runner_scheduler.stop_runner_safely(unit) is True
    queued = commands.index(["systemctl", "--no-block", "stop", unit.unit])
    terminated = commands.index(["systemctl", "kill", "--kill-who=all", "--signal=SIGTERM", unit.unit])
    resumed = commands.index(["systemctl", "kill", "--kill-who=all", "--signal=SIGCONT", unit.unit])
    waited = commands.index(["systemctl", "stop", unit.unit])
    frozen = commands.index(["systemctl", "kill", "--kill-who=all", "--signal=SIGSTOP", unit.unit])
    assert frozen < terminated < queued < resumed < waited
    assert _thaws(commands) == [unit.unit]


def test_tick_stop_also_freezes_and_reverifies(monkeypatch, tmp_path: Path) -> None:
    """apply_capacity shares the same guarded stop (DRY)."""
    units = [_active_unit(1, tmp_path), _active_unit(2, tmp_path)]
    looks = {"n": 0}

    def late_job(num: int, now: float) -> bool:
        if num != 2:
            return False
        looks["n"] += 1
        return looks["n"] >= 3

    clock, commands = _drain_env(monkeypatch, tmp_path, late_job)
    monkeypatch.setattr(
        runner_scheduler,
        "runner_busy",
        lambda workdir, **_kwargs: late_job(int(workdir.name[-1]), 0),
    )

    actions = runner_scheduler.apply_capacity(units, desired=1, dry_run=False)

    assert units[1].unit not in _stops(commands)
    assert _thaws(commands) == [units[1].unit, units[0].unit]
    assert [a["runner"] for a in actions] == [1]  # the genuinely idle runner covers the surplus


def test_failed_stop_submission_resumes_the_idle_unit(monkeypatch, tmp_path: Path) -> None:
    unit = _active_unit(2, tmp_path)
    commands: list[list[str]] = []

    def run(cmd, **_kwargs):
        commands.append(cmd)
        return _process_result(returncode=1 if cmd[:3] == ["systemctl", "--no-block", "stop"] else 0)

    monkeypatch.setattr(runner_scheduler, "run_cmd", run)
    monkeypatch.setattr(runner_scheduler, "_worker_absent", lambda *_args, **_kwargs: True)

    assert runner_scheduler.stop_runner_safely(unit) is False
    assert _thaws(commands) == [unit.unit]
    assert any("--signal=SIGTERM" in cmd for cmd in commands)
    assert _stops(commands) == []


def test_busy_worker_vetoes_stop_submission_and_termination(monkeypatch, tmp_path: Path) -> None:
    unit = _active_unit(2, tmp_path, busy=True)
    commands: list[list[str]] = []
    monkeypatch.setattr(runner_scheduler, "run_cmd", lambda cmd, **_kwargs: commands.append(cmd) or _process_result())
    monkeypatch.setattr(runner_scheduler, "_worker_absent", lambda *_args, **_kwargs: False)

    assert runner_scheduler.stop_runner_safely(unit) is False
    assert _thaws(commands) == [unit.unit]
    assert not any("stop" in cmd or "--signal=SIGTERM" in cmd for cmd in commands)


@pytest.mark.parametrize("state, expected", [("inactive", True), ("active", False), ("deactivating", False)])
def test_failed_termination_only_counts_an_already_stopped_unit(monkeypatch, tmp_path: Path, state, expected) -> None:
    unit = _active_unit(2, tmp_path)
    commands: list[list[str]] = []

    def run(cmd, **_kwargs):
        commands.append(cmd)
        if "--signal=SIGTERM" in cmd:
            return _process_result(returncode=1)
        if cmd[:2] == ["systemctl", "show"]:
            return _process_result(stdout=state + "\n")
        return _process_result()

    monkeypatch.setattr(runner_scheduler, "run_cmd", run)
    monkeypatch.setattr(runner_scheduler, "_worker_absent", lambda *_args, **_kwargs: True)

    assert runner_scheduler.stop_runner_safely(unit) is expected
    assert not any("stop" in cmd for cmd in commands)
    assert _thaws(commands) == ([] if expected else [unit.unit])


# --- #1929 item 3: maintenance drain (target 0, drain fast, report, reboot) ---


def _maintenance_env(monkeypatch, tmp_path: Path, worker_running):
    clock, commands = _drain_env(monkeypatch, tmp_path, worker_running)
    monkeypatch.setattr(runner_scheduler, "MAINTENANCE_PATH", tmp_path / "maintenance.json")
    monkeypatch.setattr(runner_scheduler, "run_cmd", lambda cmd, **_k: commands.append(cmd) or _process_result())
    return clock, commands


def test_maintenance_hold_forces_desired_zero_on_later_ticks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner_scheduler, "MAINTENANCE_PATH", tmp_path / "maintenance.json")
    config = {"enabled": True, "default_count": 4, "schedules": []}
    assert runner_scheduler.effective_capacity(config, None) == (4, "default")

    runner_scheduler.set_maintenance(True)

    assert runner_scheduler.effective_capacity(config, None) == (0, "maintenance-drain")
    runner_scheduler.set_maintenance(False)
    assert runner_scheduler.effective_capacity(config, None) == (4, "default")


def test_run_maintenance_drain_stops_all_and_reports_drained(monkeypatch, tmp_path: Path) -> None:
    units = [_active_unit(n, tmp_path, busy=True) for n in (1, 2, 3)]
    _clock, commands = _maintenance_env(monkeypatch, tmp_path, lambda num, now: now < 6 * num)

    report = runner_scheduler.run_maintenance_drain(units, timeout=120)

    assert report["drained"] is True
    assert report["remaining"] == 0
    assert sorted(_stops(commands)) == sorted(u.unit for u in units)
    assert runner_scheduler.maintenance_active() is True  # hold stays until --resume


def test_run_maintenance_drain_reports_not_drained_when_jobs_outlast_timeout(monkeypatch, tmp_path: Path) -> None:
    units = [_active_unit(n, tmp_path, busy=True) for n in (1, 2)]
    _clock, commands = _maintenance_env(monkeypatch, tmp_path, lambda num, now: num == 1)

    # Long enough for the idle runner to settle (#1973); runner 1 never goes idle.
    report = runner_scheduler.run_maintenance_drain(units, timeout=120)

    assert report["drained"] is False
    assert report["remaining"] == 1
    assert _stops(commands) == [units[1].unit]


def test_reboot_refused_unless_fully_drained(monkeypatch, tmp_path: Path) -> None:
    _clock, commands = _maintenance_env(monkeypatch, tmp_path, lambda num, now: True)

    assert runner_scheduler.reboot_if_drained({"drained": False, "remaining": 1}) is False
    assert not any(cmd[:2] == ["systemctl", "reboot"] for cmd in commands)


def test_reboot_issued_and_hold_cleared_when_drained(monkeypatch, tmp_path: Path) -> None:
    _clock, commands = _maintenance_env(monkeypatch, tmp_path, lambda num, now: False)
    runner_scheduler.set_maintenance(True)

    assert runner_scheduler.reboot_if_drained({"drained": True, "remaining": 0}) is True
    assert ["systemctl", "reboot"] in commands
    assert runner_scheduler.maintenance_active() is False  # runners return after boot


def test_main_resume_clears_hold(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner_scheduler, "MAINTENANCE_PATH", tmp_path / "maintenance.json")
    runner_scheduler.set_maintenance(True)
    config = {"enabled": True, "default_count": 1, "schedules": []}
    monkeypatch.setattr(runner_scheduler, "load_config", lambda: config)
    monkeypatch.setattr(runner_scheduler, "list_units", lambda **_kwargs: [])
    monkeypatch.setattr(runner_scheduler, "write_state", lambda _s: None)
    monkeypatch.setattr(sys, "argv", ["runner-scheduler", "--apply", "--resume"])

    assert runner_scheduler.main() == 0
    assert runner_scheduler.maintenance_active() is False


def test_stop_timeout_counts_as_stopped_when_the_unit_went_down(monkeypatch, tmp_path: Path) -> None:
    """#1973: a slow ``systemctl stop`` must not crash the scheduler run."""
    unit = _active_unit(2, tmp_path, busy=True)
    commands: list[list[str]] = []

    def run(cmd, **_k):
        commands.append(cmd)
        if cmd[:2] == ["systemctl", "stop"]:
            raise runner_scheduler.subprocess.TimeoutExpired(cmd, 30)
        if cmd[:2] == ["systemctl", "is-active"]:
            return _process_result(returncode=3)  # deactivating / inactive
        return _process_result()

    monkeypatch.setattr(runner_scheduler, "run_cmd", run)
    monkeypatch.setattr(runner_scheduler, "runner_busy_via_process", lambda _w: False)
    monkeypatch.setattr(runner_scheduler, "runner_busy_via_lock", lambda _w, _u=None, **_kwargs: False)

    assert runner_scheduler.stop_runner_safely(unit) is True
    queued = commands.index(["systemctl", "--no-block", "stop", unit.unit])
    terminated = commands.index(["systemctl", "kill", "--kill-who=all", "--signal=SIGTERM", unit.unit])
    resumed = commands.index(["systemctl", "kill", "--kill-who=all", "--signal=SIGCONT", unit.unit])
    waited = commands.index(["systemctl", "stop", unit.unit])
    assert terminated < queued < resumed < waited
    assert _thaws(commands) == [unit.unit]


def test_stop_timeout_with_unit_still_active_thaws_and_keeps_it(monkeypatch, tmp_path: Path) -> None:
    unit = _active_unit(2, tmp_path, busy=True)
    commands: list[list[str]] = []

    def run(cmd, **_k):
        commands.append(cmd)
        if cmd[:2] == ["systemctl", "stop"]:
            raise runner_scheduler.subprocess.TimeoutExpired(cmd, 30)
        return _process_result()  # is-active -> 0: still running

    monkeypatch.setattr(runner_scheduler, "run_cmd", run)
    monkeypatch.setattr(runner_scheduler, "runner_busy_via_process", lambda _w: False)
    monkeypatch.setattr(runner_scheduler, "runner_busy_via_lock", lambda _w, _u=None, **_kwargs: False)

    assert runner_scheduler.stop_runner_safely(unit) is False
    assert _thaws(commands) == [unit.unit]


def test_systemctl_stop_gets_its_own_longer_timeout(monkeypatch, tmp_path: Path) -> None:
    unit = _active_unit(2, tmp_path, busy=True)
    timeouts: dict[str, object] = {}

    def run(cmd, **kwargs):
        if cmd[:2] == ["systemctl", "stop"]:
            timeouts["stop"] = kwargs.get("timeout")
        return _process_result()

    monkeypatch.setattr(runner_scheduler, "run_cmd", run)
    monkeypatch.setattr(runner_scheduler, "runner_busy_via_process", lambda _w: False)
    monkeypatch.setattr(runner_scheduler, "runner_busy_via_lock", lambda _w, _u=None, **_kwargs: False)

    assert runner_scheduler.stop_runner_safely(unit) is True
    assert timeouts["stop"] == runner_scheduler.STOP_TIMEOUT_SECONDS
    assert runner_scheduler.STOP_TIMEOUT_SECONDS > 30


# --- #2000: host-resource floor keeps shared hosts usable for interactive work ---


def _proc_with(tmp_path: Path, *, mem_available_kb: int = 16 * 1024 * 1024, mem_psi_avg60: float = 0.0) -> Path:
    proc = tmp_path / "proc"
    (proc / "pressure").mkdir(parents=True)
    (proc / "meminfo").write_text(
        f"MemTotal:       32000000 kB\nMemFree:         1000000 kB\nMemAvailable:   {mem_available_kb} kB\n",
        encoding="utf-8",
    )
    (proc / "pressure" / "memory").write_text(
        f"some avg10=0.00 avg60={mem_psi_avg60:.2f} avg300=0.00 total=1\n"
        "full avg10=0.00 avg60=0.00 avg300=0.00 total=1\n",
        encoding="utf-8",
    )
    return proc


def _floor_env(monkeypatch, tmp_path: Path, *, disk_free_gb: float = 500.0, **proc_kwargs) -> None:
    monkeypatch.setattr(runner_scheduler, "PROC_ROOT", _proc_with(tmp_path, **proc_kwargs))
    monkeypatch.setattr(runner_scheduler, "MAINTENANCE_PATH", tmp_path / "maintenance.json")
    monkeypatch.setattr(runner_scheduler, "disk_free_gb", lambda _path: disk_free_gb)


FLOOR = {
    "disk_path": "/mnt/c",
    "min_disk_free_gb": 40,
    "min_mem_available_gb": 6,
    "max_memory_pressure_avg60": 20,
    "runners": 1,
}


def _floor_config(floor: dict | None = FLOOR) -> dict:
    config: dict = {"enabled": True, "default_count": 4, "max_count": 8, "schedules": []}
    if floor is not None:
        config["resource_floor"] = dict(floor)
    return config


def test_resource_floor_absent_keeps_schedule(monkeypatch, tmp_path: Path) -> None:
    _floor_env(monkeypatch, tmp_path, disk_free_gb=1.0, mem_available_kb=1024)
    assert runner_scheduler.effective_capacity(_floor_config(None), None) == (4, "default")


def test_resource_floor_healthy_host_keeps_schedule(monkeypatch, tmp_path: Path) -> None:
    _floor_env(monkeypatch, tmp_path)
    assert runner_scheduler.effective_capacity(_floor_config(), None) == (4, "default")


def test_resource_floor_low_disk_lowers_capacity(monkeypatch, tmp_path: Path) -> None:
    _floor_env(monkeypatch, tmp_path, disk_free_gb=30.0)
    desired, reason = runner_scheduler.effective_capacity(_floor_config(), None)
    assert desired == 1
    assert reason.startswith("default + resource-floor")
    assert "disk_free=30.0GB<40" in reason


def test_resource_floor_low_mem_available_lowers_capacity(monkeypatch, tmp_path: Path) -> None:
    _floor_env(monkeypatch, tmp_path, mem_available_kb=4 * 1024 * 1024)
    desired, reason = runner_scheduler.effective_capacity(_floor_config(), None)
    assert desired == 1
    assert "mem_available=4.0GB<6" in reason


def test_resource_floor_memory_pressure_lowers_capacity(monkeypatch, tmp_path: Path) -> None:
    _floor_env(monkeypatch, tmp_path, mem_psi_avg60=35.5)
    desired, reason = runner_scheduler.effective_capacity(_floor_config(), None)
    assert desired == 1
    assert "memory_psi_avg60=35.5>20" in reason


def test_resource_floor_never_raises_capacity(monkeypatch, tmp_path: Path) -> None:
    _floor_env(monkeypatch, tmp_path, disk_free_gb=1.0)
    config = _floor_config({**FLOOR, "runners": 3})
    config["default_count"] = 2
    assert runner_scheduler.effective_capacity(config, None)[0] == 2


def test_resource_floor_applies_to_manual_target(monkeypatch, tmp_path: Path) -> None:
    _floor_env(monkeypatch, tmp_path, disk_free_gb=10.0)
    assert runner_scheduler.effective_capacity(_floor_config(), 6)[0] == 1


def test_resource_floor_unreadable_probe_is_not_a_breach(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner_scheduler, "PROC_ROOT", tmp_path / "missing-proc")
    monkeypatch.setattr(runner_scheduler, "MAINTENANCE_PATH", tmp_path / "maintenance.json")
    monkeypatch.setattr(runner_scheduler, "disk_free_gb", lambda _path: None)
    assert runner_scheduler.effective_capacity(_floor_config(), None) == (4, "default")


def test_maintenance_hold_beats_resource_floor(monkeypatch, tmp_path: Path) -> None:
    _floor_env(monkeypatch, tmp_path, disk_free_gb=1.0)
    (tmp_path / "maintenance.json").write_text("{}", encoding="utf-8")
    assert runner_scheduler.effective_capacity(_floor_config(), None) == (0, "maintenance-drain")


def test_disk_free_gb_reads_real_path(tmp_path: Path) -> None:
    free = runner_scheduler.disk_free_gb(str(tmp_path))
    assert free is not None and free > 0
    assert runner_scheduler.disk_free_gb(str(tmp_path / "does-not-exist")) is None


def test_validate_config_rejects_bad_resource_floor() -> None:
    import pytest

    for bad in ({"min_disk_free_gb": -1}, {"runners": -1}, {"max_memory_pressure_avg60": "high"}, "nope"):
        with pytest.raises(ValueError):
            runner_scheduler.validate_config(_floor_config(bad))  # type: ignore[arg-type]


def test_oglaptop_schedule_carries_conservative_resource_floor() -> None:
    config = json.loads((REPO_ROOT / "config" / "runner-schedule-oglaptop.json").read_text(encoding="utf-8"))
    runner_scheduler.validate_config(config)
    floor = config["resource_floor"]
    assert floor["disk_path"] == "/mnt/c"
    assert floor["min_disk_free_gb"] >= 40
    assert floor["min_mem_available_gb"] >= 6
    assert floor["runners"] <= 1


# --- #2776: a "successful" stop must not leave frozen children in the cgroup ---


def _leftover_env(monkeypatch, tmp_path: Path, unit, *, pids: list[int], die_on: str | None = "SIGTERM"):
    """Mock systemctl plus a fake cgroup; ``die_on`` is the signal that empties it."""
    cg_root = tmp_path / "cgroup"
    procs = cg_root / "system.slice" / unit.unit / "cgroup.procs"
    procs.parent.mkdir(parents=True)
    procs.write_text("".join(f"{p}\n" for p in pids), encoding="utf-8")
    commands: list[list[str]] = []
    signals: list[tuple[int, str]] = []

    def run(cmd, **_k):
        commands.append(cmd)
        if cmd[:2] == ["systemctl", "show"] and "ControlGroup" in cmd:
            return _process_result(stdout=f"/system.slice/{unit.unit}\n")
        return _process_result()

    def signal_pid(pid: int, sig: str) -> None:
        signals.append((pid, sig))
        if sig == die_on:
            procs.write_text("", encoding="utf-8")

    monkeypatch.setattr(runner_scheduler, "CGROUP_ROOT", cg_root)
    monkeypatch.setattr(runner_scheduler, "run_cmd", run)
    monkeypatch.setattr(runner_scheduler, "_signal_pid", signal_pid)
    monkeypatch.setattr(runner_scheduler, "LEFTOVER_TERM_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(runner_scheduler.time_module, "sleep", lambda _s: None)
    monkeypatch.setattr(runner_scheduler, "_worker_absent", lambda *_a, **_k: True)
    return commands, signals


def test_leftover_frozen_children_are_thawed_and_terminated_after_stop(monkeypatch, tmp_path: Path) -> None:
    unit = _active_unit(2, tmp_path)
    commands, signals = _leftover_env(monkeypatch, tmp_path, unit, pids=[4101, 4102])

    assert runner_scheduler.stop_runner_safely(unit) is True

    assert [s for s in signals if s[1] == "SIGCONT"] == [(4101, "SIGCONT"), (4102, "SIGCONT")]
    assert [s for s in signals if s[1] == "SIGTERM"] == [(4101, "SIGTERM"), (4102, "SIGTERM")]
    assert not any(s[1] == "SIGKILL" for s in signals)
    assert commands.index(["systemctl", "stop", unit.unit]) < commands.index(["systemctl", "reset-failed", unit.unit])


def test_leftover_children_that_ignore_term_are_killed(monkeypatch, tmp_path: Path) -> None:
    unit = _active_unit(2, tmp_path)
    _commands, signals = _leftover_env(monkeypatch, tmp_path, unit, pids=[4101], die_on="SIGKILL")

    assert runner_scheduler.stop_runner_safely(unit) is True

    order = [sig for _pid, sig in signals]
    assert order.index("SIGTERM") < order.index("SIGKILL")


def test_clean_cgroup_after_stop_signals_nothing(monkeypatch, tmp_path: Path) -> None:
    unit = _active_unit(2, tmp_path)
    _commands, signals = _leftover_env(monkeypatch, tmp_path, unit, pids=[])

    assert runner_scheduler.stop_runner_safely(unit) is True
    assert signals == []


def test_timeout_path_reaps_leftovers_instead_of_leaving_them_frozen(monkeypatch, tmp_path: Path) -> None:
    unit = _active_unit(2, tmp_path)
    commands, signals = _leftover_env(monkeypatch, tmp_path, unit, pids=[4101])
    inner = runner_scheduler.run_cmd

    def run(cmd, **kwargs):
        if cmd[:2] == ["systemctl", "stop"]:
            commands.append(cmd)
            raise runner_scheduler.subprocess.TimeoutExpired(cmd, 30)
        if cmd[:2] == ["systemctl", "is-active"]:
            return _process_result(returncode=3)
        return inner(cmd, **kwargs)

    monkeypatch.setattr(runner_scheduler, "run_cmd", run)

    assert runner_scheduler.stop_runner_safely(unit) is True
    assert (4101, "SIGCONT") in signals and (4101, "SIGTERM") in signals


def test_busy_runner_is_never_reaped_or_stopped(monkeypatch, tmp_path: Path) -> None:
    unit = _active_unit(2, tmp_path, busy=True)
    commands, signals = _leftover_env(monkeypatch, tmp_path, unit, pids=[4101])
    monkeypatch.setattr(runner_scheduler, "_worker_absent", lambda *_a, **_k: False)

    assert runner_scheduler.stop_runner_safely(unit) is False

    assert signals == []
    assert _thaws(commands) == [unit.unit]
    assert _stops(commands) == []


def test_failed_stop_keeps_children_alive(monkeypatch, tmp_path: Path) -> None:
    """When the unit stays up, its processes are healthy and must not be signalled."""
    unit = _active_unit(2, tmp_path)
    commands, signals = _leftover_env(monkeypatch, tmp_path, unit, pids=[4101])
    inner = runner_scheduler.run_cmd

    def run(cmd, **kwargs):
        if cmd[:2] == ["systemctl", "stop"]:
            return _process_result(returncode=1)
        return inner(cmd, **kwargs)

    monkeypatch.setattr(runner_scheduler, "run_cmd", run)

    assert runner_scheduler.stop_runner_safely(unit) is False
    assert signals == []
    assert _thaws(commands) == [unit.unit]
