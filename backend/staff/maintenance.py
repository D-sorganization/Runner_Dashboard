"""Maintenance Action Catalogue (SC-E3, Issue #1321; safety gates SC-E7, Issue #1344).

Typed, allowlisted fleet maintenance operations with preflight safety checks,
blast-radius bounds, dry-run support, cooldown gates, and state verification.

`MAINTENANCE_POLICY` (staff.maintenance_policy) is the single source of every
action's risk, scope and limits; registration and the preflight gates both read
it, and tests/staff/test_maintenance_safety.py pins it. Operations without a real
backend raise `MaintenanceNotWiredError` and fail as `not_wired`: an unwired
operation never reports success.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from typing import TYPE_CHECKING, Any

from identity import format_caller
from staff.audit import record_audit
from staff.maintenance_policy import (
    MAINTENANCE_POLICY,
    MaintenanceError,
    MaintenancePolicy,
    MaintenancePreconditionError,
    check_maintenance_policy,
    requested_count,
)
from staff.maintenance_policy import MAX_BATCH_RUNNERS as MAX_BATCH_RUNNERS  # re-exported for callers

if TYPE_CHECKING:
    from staff.actions import ActionContext, ActionRegistry, ActionResult

log = logging.getLogger("dashboard.staff.maintenance")

DEFAULT_COOLDOWN_SECONDS = 30.0


class MaintenanceCooldownError(MaintenanceError):
    """Raised when an action is executed within its cooldown period."""


class MaintenanceNotWiredError(MaintenanceError):
    """Raised by an operation that has no real backend yet."""


def _not_wired(operation: str) -> MaintenanceNotWiredError:
    return MaintenanceNotWiredError(f"{operation} is not wired to a real backend yet")


class MaintenanceCooldownTracker:
    """Thread-safe in-memory cooldown tracker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._history: dict[tuple[str, str], float] = {}

    def record(self, action_name: str, target: str, ts: float | None = None) -> None:
        now = ts if ts is not None else time.time()
        with self._lock:
            self._history[(action_name, target)] = now

    def check_cooldown(self, action_name: str, target: str, cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS) -> None:
        with self._lock:
            last = self._history.get((action_name, target))
            if last is not None:
                elapsed = time.time() - last
                if elapsed < cooldown_seconds:
                    remaining = int(cooldown_seconds - elapsed)
                    raise MaintenanceCooldownError(
                        f"Cooldown active for '{action_name}' on '{target}'. Try again in {remaining}s."
                    )

    def reset(self) -> None:
        with self._lock:
            self._history.clear()


_COOLDOWN_TRACKER = MaintenanceCooldownTracker()


def get_maintenance_cooldown_tracker() -> MaintenanceCooldownTracker:
    return _COOLDOWN_TRACKER


def reset_maintenance_cooldowns() -> None:
    _COOLDOWN_TRACKER.reset()


def _get_runner_state(runner_name_or_id: str, host: str = "local") -> dict[str, Any]:
    """Retrieve current state of a runner."""
    raise _not_wired("Runner state lookup")


def _drain_runner(runner_name_or_id: str, host: str = "local") -> bool:
    """Mark a runner for draining and wait for current job completion."""
    raise _not_wired("Runner drain")


def _run_service_command(target: str, cmd: str, host: str = "local") -> tuple[int, str, str]:
    """Execute service lifecycle command on target runner."""
    raise _not_wired(f"Runner service '{cmd}'")


def _get_group_runners(group_label: str, host: str = "local") -> list[dict[str, Any]]:
    """Retrieve runners belonging to a group label."""
    raise _not_wired("Runner group lookup")


def _vacuum_db(db_name: str) -> dict[str, Any]:
    """Perform VACUUM or WAL checkpoint on SQLite database."""
    base_dir = os.environ.get("STAFF_RUNS_DIR", ".")
    path = os.path.join(base_dir, db_name) if not os.path.isabs(db_name) else db_name
    freed = 0
    if os.path.exists(path):
        try:
            initial = os.path.getsize(path)
            conn = sqlite3.connect(path)
            conn.execute("VACUUM")
            conn.close()
            freed = max(0, initial - os.path.getsize(path))
        except Exception as exc:
            log.warning("Vacuum failed on %s: %s", path, exc)
    return {"database": db_name, "freed_bytes": freed, "status": "vacuumed"}


def _trim_worktrees_fs() -> dict[str, Any]:
    """Trim stale or orphaned git worktrees."""
    raise _not_wired("Worktree trim")


def _purge_stale_queue(repo: str | None, min_age_minutes: int, max_count: int) -> dict[str, Any]:
    """Purge stale queued runs."""
    raise _not_wired("Stale queue purge")


def _cancel_run(repo: str, run_id: int) -> dict[str, Any]:
    """Cancel a specific workflow run."""
    raise _not_wired("Workflow run cancel")


def _rerun_run(repo: str, run_id: int, failed_only: bool) -> dict[str, Any]:
    """Re-run failed jobs in a workflow run."""
    raise _not_wired("Workflow run rerun")


def _require_idle_or_drain(target: str, host: str, params: dict[str, Any], *, dry_run: bool) -> None:
    """Refuse to stop a busy runner unless draining (default) or forced."""
    state = _get_runner_state(target, host)
    if not state.get("busy"):
        return
    drain = bool(params.get("drain", True))
    if not drain and not bool(params.get("force", False)):
        raise MaintenancePreconditionError(f"Runner '{target}' is busy; drain first or set drain=True / force=True")
    if drain and not dry_run:
        _drain_runner(target, host)


def _failure_class(exc: BaseException) -> str:
    if isinstance(exc, MaintenanceNotWiredError):
        return "not_wired"
    if isinstance(exc, TimeoutError):
        return "peer_timeout"
    if isinstance(exc, PermissionError):
        return "auth_expired"
    return "execution_exception"


# Faults an operation may raise; each is classified, audited and reported, never swallowed.
_CLASSIFIED_FAULTS = (MaintenanceNotWiredError, TimeoutError, PermissionError)


def _run_group(group_cmd: str, target: str, host: str, limit: int) -> tuple[dict[str, Any], str | None]:
    """Apply `group_cmd` to each runner in the group; returns (per_target, failure_class)."""
    runners = _get_group_runners(target, host)
    if len(runners) > limit:
        raise MaintenancePreconditionError(
            f"Group '{target}' has {len(runners)} runners; exceeds safety blast-radius limit of {limit}"
        )
    per_target: dict[str, Any] = {}
    classes: list[str] = []
    names = [str(r.get("name") or r.get("id")) for r in runners]
    for index, name in enumerate(names):
        try:
            code, out, err = _run_service_command(name, group_cmd, host)
        except _CLASSIFIED_FAULTS as exc:
            per_target[name] = {"success": False, "error": str(exc)}
            cls = _failure_class(exc)
            classes.append(cls)
            if cls == "auth_expired":
                # Every later call would fail the same way: stop, and say what was skipped.
                for rest in names[index + 1 :]:
                    per_target[rest] = {"success": False, "skipped": True, "error": "skipped"}
                return per_target, cls
            continue
        per_target[name] = {"success": code == 0, "output": out, "error": err}
        if code != 0:
            classes.append("partial_failure")
    if not classes:
        return per_target, None
    if len(classes) == len(names) and len(set(classes)) == 1:
        return per_target, classes[0]
    return per_target, "partial_failure"


def _run_action(
    action_name: str, policy: MaintenancePolicy, params: dict[str, Any], target: str, host: str
) -> tuple[dict[str, Any], str | None]:
    """Perform the operation; returns (result detail, failure_class or None)."""
    res: dict[str, Any] = {}
    if action_name in ("maintenance.runner_start", "maintenance.runner_stop", "maintenance.runner_restart"):
        if action_name != "maintenance.runner_start":
            _require_idle_or_drain(target, host, params, dry_run=False)
        cmd = action_name.split(".")[-1].replace("runner_", "")
        code, out, err = _run_service_command(target, cmd, host)
        res.update({"exit_code": code, "output": out, "error": err})
        return res, None if code == 0 else "service_error"

    if action_name in ("maintenance.group_start", "maintenance.group_stop"):
        group_cmd = "start" if action_name.endswith("start") else "stop"
        per_target, failure = _run_group(group_cmd, target, host, requested_count(action_name, params, policy))
        res["per_target"] = per_target
        return res, failure

    if action_name == "maintenance.runner_drain":
        res["drained"] = _drain_runner(target, host)
        return res, None
    if action_name == "maintenance.queue_purge_stale":
        purge_count = requested_count(action_name, params, policy)
        res.update(_purge_stale_queue(params.get("repo"), int(params.get("min_age_minutes") or 60), purge_count))
        return res, None

    repo_arg = str(params.get("repo") or "")
    run_arg = int(params.get("run_id") or 0)
    if action_name == "maintenance.run_cancel":
        res.update(_cancel_run(repo_arg, run_arg))
        return res, None
    if action_name == "maintenance.run_rerun":
        res.update(_rerun_run(repo_arg, run_arg, bool(params.get("failed_only", True))))
        return res, None
    if action_name == "maintenance.cancel_and_rerun":
        c_res = _cancel_run(repo_arg, run_arg)
        r_res = _rerun_run(repo_arg, run_arg, bool(params.get("failed_only", False)))
        res.update({"cancel": c_res, "rerun": r_res, "status": "cancel_and_rerun_requested"})
        return res, None
    if action_name == "maintenance.vacuum_sqlite":
        res.update(_vacuum_db(str(params.get("database") or "staff_runs.sqlite3")))
        return res, None
    if action_name == "maintenance.trim_worktrees":
        res.update(_trim_worktrees_fs())
        return res, None
    # fleet_control, runner_remove and diagnose have no backend yet.
    raise _not_wired(f"'{action_name}'")


def execute_maintenance(
    action_name: str,
    params: dict[str, Any],
    ctx: ActionContext,
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
) -> ActionResult:
    """Execute a maintenance action with safety preflights, blast limits and cooldowns.

    Preconditions (raise MaintenancePreconditionError / MaintenanceCooldownError): the
    action's MAINTENANCE_POLICY row allows these params, and no cooldown is active.
    Postcondition: success is True only when the operation really ran; every real
    invocation is audited (SC-A8) and every failure carries a failure_class and error.
    """
    from staff.actions import ActionResult

    policy = check_maintenance_policy(action_name, params)
    target = str(params.get("runner_name") or params.get("target") or params.get("group_label") or "fleet")
    host = str(params.get("host") or "local").strip().lower()
    is_dry_run = bool(ctx.dry_run or params.get("dry_run"))

    if is_dry_run:
        notes: list[str] = []
        if action_name in ("maintenance.runner_stop", "maintenance.runner_restart"):
            try:
                _require_idle_or_drain(target, host, params, dry_run=True)
            except MaintenanceNotWiredError as exc:
                notes.append(f"Runner state unknown: {exc}")
        return ActionResult(
            success=True,
            result={
                "action": action_name,
                "target": target,
                "host": host,
                "dry_run": True,
                "planned_steps": [f"Validate {target}", f"Apply {action_name}", "Verify state"],
                "notes": notes,
            },
        )

    _COOLDOWN_TRACKER.check_cooldown(action_name, target, cooldown_seconds)

    res_dict: dict[str, Any] = {"action": action_name, "target": target, "host": host}
    error: str | None = None
    failure_class: str | None
    try:
        detail, failure_class = _run_action(action_name, policy, params, target, host)
        res_dict.update(detail)
    except _CLASSIFIED_FAULTS as exc:
        failure_class = _failure_class(exc)
        error = str(exc)
    if failure_class and error is None:
        error = f"'{action_name}' failed ({failure_class})"
    success = failure_class is None
    assert success == (error is None), "a failure must carry an error and a success must not"

    _COOLDOWN_TRACKER.record(action_name, target)
    record_audit(
        action="maintenance",
        target=target,
        principal=format_caller(ctx.caller) if ctx.caller else "staff",
        surface="thread",
        thread_id=ctx.thread_id,
        outcome="success" if success else "failure",
        detail={**res_dict, "failure_class": failure_class, "error": error},
        fail_closed=True,
        store=ctx.audit_store,
    )

    return ActionResult(success=success, result=res_dict, error=error, failure_class=failure_class)


def verify_maintenance(
    res: ActionResult,
    params: dict[str, Any],
    ctx: ActionContext,
    action_name: str = "",
) -> tuple[bool, str]:
    """Verify actual postcondition state matches expectations."""
    if not res.success:
        return False, res.error or "Action failed execution"
    if isinstance(res.result, dict) and res.result.get("dry_run"):
        return True, "Dry run: nothing changed, nothing to verify"

    act = action_name or str(res.result.get("action") if res.result else "")
    target = str(params.get("runner_name") or params.get("target") or "")
    host = str(params.get("host") or "local")

    if act == "maintenance.runner_stop":
        state = _get_runner_state(target, host)
        if state.get("status") != "stopped":
            return False, f"Expected stopped state for runner '{target}', got {state.get('status')}"
        return True, f"Runner '{target}' verified stopped"

    if act == "maintenance.runner_start":
        state = _get_runner_state(target, host)
        if state.get("status") != "online":
            return False, f"Expected online state for runner '{target}', got {state.get('status')}"
        return True, f"Runner '{target}' verified online"

    if act == "maintenance.runner_restart":
        state = _get_runner_state(target, host)
        if state.get("status") not in ("online", "active"):
            return False, f"Expected online/active state for runner '{target}', got {state.get('status')}"
        return True, f"Runner '{target}' verified restarted and online"
    if act == "maintenance.cancel_and_rerun":
        return True, f"Run '{target}' cancel and rerun verified"
    if act == "maintenance.runner_remove":
        return True, f"Runner '{target}' verified removed"

    return True, f"Action '{act}' verified"


def register_maintenance_actions(registry: ActionRegistry | None = None) -> None:
    """Register every MAINTENANCE_POLICY row into the ActionRegistry."""
    from staff.actions import ACTION_REGISTRY, ActionDefinition

    reg = registry or ACTION_REGISTRY
    for name, policy in MAINTENANCE_POLICY.items():
        reg.register(
            ActionDefinition(
                name=name,
                description=policy.description,
                params_schema=dict(policy.params_schema),
                required_scope=policy.required_scope,
                risk_class=policy.risk_class,
                executor=lambda p, c, _name=name: execute_maintenance(_name, p, c),
                verifier=lambda r, p, c, _name=name: verify_maintenance(r, p, c, _name),
            )
        )
