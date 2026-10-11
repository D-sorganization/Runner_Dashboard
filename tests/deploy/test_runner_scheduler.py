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
    monkeypatch.setattr(runner_scheduler, "runner_busy_via_lock", lambda workdir, unit=None: False)
    return clock, commands


def _stops(commands: list[list[str]]) -> list[str]:
    return [cmd[2] for cmd in commands if cmd[:2] == ["systemctl", "stop"]]


def test_drain_stops_runner_that_is_between_jobs_even_when_tick_saw_all_busy(monkeypatch, tmp_path: Path) -> None:
    """The #1929 race: every runner is busy at the tick, but one gaps between jobs."""
    units = [_active_unit(n, tmp_path, busy=True) for n in (1, 2, 3, 4)]
    # Runner 4 has a ~5 s gap between jobs starting at t=20; the others never gap.
    clock, commands = _drain_env(monkeypatch, tmp_path, lambda num, now: not (num == 4 and 20 <= now < 25))

    actions = runner_scheduler.drain_surplus(units, desired=3, dry_run=False)

    assert [a["runner"] for a in actions] == [4]
    assert actions[0]["action"] == "stop"
    assert _stops(commands) == [units[3].unit]


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
    monkeypatch.setattr(runner_scheduler, "list_units", lambda: units)
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

    def failing_freeze(cmd, **_k):
        commands.append(cmd)
        return _process_result(returncode=1 if "--signal=SIGSTOP" in cmd else 0)

    monkeypatch.setattr(runner_scheduler, "run_cmd", failing_freeze)

    assert runner_scheduler.drain_surplus(units, desired=1, dry_run=False) == []
    assert _stops(commands) == []


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
    monkeypatch.setattr(runner_scheduler, "runner_busy", lambda workdir, unit=None: late_job(int(workdir.name[-1]), 0))

    actions = runner_scheduler.apply_capacity(units, desired=1, dry_run=False)

    assert units[1].unit not in _stops(commands)
    assert _thaws(commands) == [units[1].unit]
    assert [a["runner"] for a in actions] == [1]  # the genuinely idle runner covers the surplus


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

    report = runner_scheduler.run_maintenance_drain(units, timeout=30)

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
    monkeypatch.setattr(runner_scheduler, "list_units", lambda: [])
    monkeypatch.setattr(runner_scheduler, "write_state", lambda _s: None)
    monkeypatch.setattr(sys, "argv", ["runner-scheduler", "--apply", "--resume"])

    assert runner_scheduler.main() == 0
    assert runner_scheduler.maintenance_active() is False
