"""Deterministic offline replay of the RD#4682 ``merge_group`` failure (#4683).

The failing check was ``tests/unit/test_scheduler_retry_wp16.py::
test_watchdog_waits_for_orphan_then_expires``. It is not tree-dependent: the
merge-queue tree equalled the green PR head tree. The failure is a scheduling
race in ``staff.watchdog.StaffWatchdog``: the sampler only records child PIDs
while the leader is alive, so a sampler that is starved past the leader's exit
never learns about the orphan and exits with ``failure_class=None``.

``replay_case`` drives the *real* ``StaffWatchdog._run_loop`` against a scripted
world on a virtual clock (no threads, no processes, no sleeping), so the same
recorded wake schedule always yields the same verdict.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from staff import watchdog as watchdog_mod

LEADER_PID = 101
CHILD_PID = 102


class _NoSuchProcess(Exception):
    pass


class _World:
    """Virtual clock plus a scripted leader/orphan process pair."""

    def __init__(self, world: dict[str, float], wake_times: list[float]) -> None:
        self.w = world
        self.wakes = list(wake_times)
        self.now = self.wakes[0]
        self.terminated: list[int] = []

    def leader_alive(self) -> bool:
        return self.now < self.w["leader_exit_at"]

    def child_alive(self) -> bool:
        return self.w["child_spawn_at"] <= self.now < self.w["child_exit_at"]

    def pid_exists(self, pid: int) -> bool:
        return self.leader_alive() if pid == LEADER_PID else self.child_alive()

    def next_wake(self) -> bool:
        self.wakes.pop(0)
        if not self.wakes:
            return False
        self.now = self.wakes[0]
        return True


class _Proc:
    pid = LEADER_PID

    def __init__(self, world: _World) -> None:
        self._world = world

    def poll(self) -> int | None:
        return None if self._world.leader_alive() else 0


class _Stop:
    """Stands in for ``threading.Event``: each ``wait`` is one virtual wake."""

    def __init__(self, world: _World) -> None:
        self._world = world
        self._done = False

    def is_set(self) -> bool:
        return self._done

    def wait(self, _timeout: float | None = None) -> bool:
        self._done = not self._world.next_wake()
        return self._done


class _Store:
    def append_event(self, *_a: Any, **_k: Any) -> None:
        return None

    def update_run(self, *_a: Any, **_k: Any) -> None:
        return None


def _fake_psutil(world: _World) -> Any:
    class _Child:
        pid = CHILD_PID

    class _Leader:
        pid = LEADER_PID

        def children(self, recursive: bool = False) -> list[_Child]:
            return [_Child()] if world.child_alive() else []

    class _Psutil:
        NoSuchProcess = _NoSuchProcess
        AccessDenied = PermissionError

        @staticmethod
        def Process(_pid: int) -> _Leader:  # noqa: N802 - mirrors psutil API
            if not world.leader_alive():
                raise _NoSuchProcess
            return _Leader()

        @staticmethod
        def pid_exists(pid: int) -> bool:
            return world.pid_exists(pid)

    return _Psutil


def replay_case(case: dict[str, Any]) -> dict[str, Any]:
    """Replay one recorded case and return its verdict (pure, repeatable)."""
    world = _World(case["world"], case["post_hoc"]["wake_times"])
    wd = watchdog_mod.StaffWatchdog(
        run_id="replay",
        proc=_Proc(world),  # type: ignore[arg-type]
        store=_Store(),  # type: ignore[arg-type]
        max_seconds=30.0,
        idle_seconds=30.0,
        orphan_grace_seconds=case["world"]["orphan_grace_seconds"],
        check_interval=0.05,
        time_fn=lambda: world.now,
    )
    wd._stop_event = _Stop(world)  # type: ignore[assignment]
    saved = (watchdog_mod.psutil, watchdog_mod.terminate_process_group)
    watchdog_mod.psutil = _fake_psutil(world)  # type: ignore[assignment]
    watchdog_mod.terminate_process_group = lambda pid, grace_period=3.0: (  # type: ignore[assignment]
        world.terminated.extend(sorted(wd._tracked_pids)) or True
    )
    try:
        wd._run_loop()
    finally:
        watchdog_mod.psutil, watchdog_mod.terminate_process_group = saved
    return {
        "failure_class": wd.failure_class,
        "outcome": "pass" if wd.failure_class == "stalled" else "fail",
        "virtual_elapsed_s": round(world.now - case["post_hoc"]["wake_times"][0], 3),
        "terminated_pids": [p for p in world.terminated if p != LEADER_PID],
    }


def _pre_check_features(case: dict[str, Any]) -> dict[str, Any]:
    pre = case["pre_check"]
    return {
        "tree_equals_green_pr_head": pre["merge_group_tree"] == pre["pr_head_tree"],
        "workflow_blob": pre["workflow_blob"],
        "diff_touches_watchdog": any("watchdog" in f for f in pre["changed_files"]),
        "pr_checks_green": pre["pr_checks_green"],
    }


def evaluate_stop_gate(failing: dict[str, Any], calibration: dict[str, Any]) -> dict[str, Any]:
    """Issue #4683 stop gate: is the failure derivable before the check ends?"""
    discriminates = _pre_check_features(failing) != _pre_check_features(calibration)
    schedule_pre = bool(failing["post_hoc"].get("available_pre_check", False))
    blocked = not (discriminates or schedule_pre)
    return {
        "decision": "STOP_BEFORE_SHADOW" if blocked else "PROCEED",
        "pre_check_discriminates": discriminates,
        "schedule_available_pre_check": schedule_pre,
    }


def workflow_triggers(workflow: Path) -> set[str]:
    data = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    return set((data.get(True) or data.get("on") or {}).keys())


def classify_job_events(workflow: Path) -> dict[str, str]:
    """Map each job id to ``pull_request_only``, ``not_merge_group`` or ``both``."""
    jobs = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]
    table: dict[str, str] = {}
    for job_id, spec in jobs.items():
        cond = re.sub(r"\s+", " ", str(spec.get("if", "")))
        if "!= 'merge_group'" in cond:
            table[job_id] = "not_merge_group"
        elif "== 'pull_request'" in cond and "!= 'pull_request'" not in cond:
            table[job_id] = "pull_request_only"
        else:
            table[job_id] = "both"
    return table
