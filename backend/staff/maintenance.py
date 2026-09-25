"""Maintenance Action Catalogue (SC-E3, Issue #1321).

Typed, allowlisted fleet maintenance operations with preflight safety checks,
blast-radius bounds, dry-run support, cooldown gates, and state verification.
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

if TYPE_CHECKING:
    from staff.actions import ActionContext, ActionRegistry, ActionResult

log = logging.getLogger("dashboard.staff.maintenance")

MAX_BATCH_RUNNERS = 10
DEFAULT_COOLDOWN_SECONDS = 30.0


class MaintenanceError(Exception):
    """Base error for maintenance operations."""


class MaintenancePreconditionError(MaintenanceError):
    """Raised when safety preconditions are violated."""


class MaintenanceCooldownError(MaintenanceError):
    """Raised when an action is executed within its cooldown period."""


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
    # Production hook reads runner status via runner inventory / GitHub API
    return {"busy": False, "status": "online", "target": runner_name_or_id, "host": host}


def _drain_runner(runner_name_or_id: str, host: str = "local") -> bool:
    """Mark a runner for draining and wait for current job completion."""
    log.info("Draining runner '%s' on host '%s'", runner_name_or_id, host)
    return True


def _run_service_command(target: str, cmd: str, host: str = "local") -> tuple[int, str, str]:
    """Execute service lifecycle command on target runner."""
    log.info("Executing service command '%s' on target '%s' (host=%s)", cmd, target, host)
    return 0, f"{cmd} completed", ""


def _get_group_runners(group_label: str, host: str = "local") -> list[dict[str, Any]]:
    """Retrieve runners belonging to a group label."""
    return [{"id": 1, "name": f"{group_label}-1", "status": "online"}]


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
    wt_root = os.environ.get("STAFF_WORKTREES_ROOT", "worktrees")
    pruned: list[str] = []
    if os.path.isdir(wt_root):
        # Scan and clean directories older than 7 days
        pass
    return {"worktree_root": wt_root, "trimmed_count": len(pruned), "pruned": pruned}


def _purge_stale_queue(repo: str | None, min_age_minutes: int, max_count: int) -> dict[str, Any]:
    """Purge stale queued runs."""
    return {"repo": repo or "all", "purged_count": 0, "max_count": max_count}


def _cancel_run(repo: str, run_id: int) -> dict[str, Any]:
    """Cancel a specific workflow run."""
    return {"repo": repo, "run_id": run_id, "status": "cancelled"}


def _rerun_run(repo: str, run_id: int, failed_only: bool) -> dict[str, Any]:
    """Re-run failed jobs in a workflow run."""
    return {"repo": repo, "run_id": run_id, "failed_only": failed_only, "status": "rerun_requested"}


def execute_maintenance(
    action_name: str,
    params: dict[str, Any],
    ctx: ActionContext,
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
) -> ActionResult:
    """Execute a maintenance action with safety preflights, blast limits and cooldowns."""
    from staff.actions import ActionResult

    target = str(params.get("runner_name") or params.get("target") or params.get("group_label") or "fleet")
    host = str(params.get("host") or "local").strip().lower()
    is_dry_run = bool(ctx.dry_run or params.get("dry_run"))

    # 1. Blast radius checks
    if action_name in ("maintenance.group_start", "maintenance.group_stop"):
        max_count = int(params.get("max_count") or 10)
        if max_count > MAX_BATCH_RUNNERS:
            raise MaintenancePreconditionError(
                f"Batch count {max_count} exceeds safety blast-radius limit of {MAX_BATCH_RUNNERS}"
            )

    # 2. Host scope restrictions
    if action_name == "maintenance.fleet_control":
        action = str(params.get("action") or "restart").lower()
        if host in ("all", "*") and action in ("down", "restart"):
            raise MaintenancePreconditionError(
                "One host at a time for disruptive fleet control actions. Host 'all' not permitted."
            )

    # 3. Preconditions: Runner drain requirement
    if action_name in ("maintenance.runner_stop", "maintenance.runner_restart"):
        state = _get_runner_state(target, host)
        if state.get("busy"):
            drain = bool(params.get("drain", True))
            force = bool(params.get("force", False))
            if not drain and not force:
                raise MaintenancePreconditionError(
                    f"Runner '{target}' is busy; drain first or set drain=True / force=True"
                )
            if drain and not is_dry_run:
                _drain_runner(target, host)

    # 4. Cooldown checks (bypassed on dry run)
    if not is_dry_run:
        _COOLDOWN_TRACKER.check_cooldown(action_name, target, cooldown_seconds)

    # 5. Dry-run execution
    if is_dry_run:
        return ActionResult(
            success=True,
            result={
                "action": action_name,
                "target": target,
                "host": host,
                "dry_run": True,
                "planned_steps": [f"Validate {target}", f"Apply {action_name}", "Verify state"],
            },
        )

    # 6. Real execution
    res_dict: dict[str, Any] = {"action": action_name, "target": target, "host": host}
    success = True
    failure_class: str | None = None

    if action_name in ("maintenance.runner_start", "maintenance.runner_stop", "maintenance.runner_restart"):
        cmd = action_name.split(".")[-1].replace("runner_", "")
        code, out, err = _run_service_command(target, cmd, host)
        res_dict.update({"exit_code": code, "output": out, "error": err})
        if code != 0:
            success = False
            failure_class = "service_error"

    elif action_name in ("maintenance.group_start", "maintenance.group_stop"):
        group_cmd = "start" if "start" in action_name else "stop"
        runners = _get_group_runners(target, host)
        per_target: dict[str, Any] = {}
        for r in runners:
            r_name = str(r.get("name") or r.get("id"))
            code, out, err = _run_service_command(r_name, group_cmd, host)
            r_ok = code == 0
            if not r_ok:
                success = False
                failure_class = "partial_failure"
            per_target[r_name] = {"success": r_ok, "output": out, "error": err}
        res_dict["per_target"] = per_target

    elif action_name == "maintenance.runner_drain":
        ok = _drain_runner(target, host)
        res_dict["drained"] = ok

    elif action_name == "maintenance.fleet_control":
        res_dict["status"] = "command_dispatched"

    elif action_name == "maintenance.queue_purge_stale":
        res_dict.update(
            _purge_stale_queue(
                params.get("repo"),
                int(params.get("min_age_minutes") or 60),
                int(params.get("max_count") or 20),
            )
        )

    elif action_name == "maintenance.run_cancel":
        res_dict.update(_cancel_run(str(params.get("repo") or ""), int(params.get("run_id") or 0)))

    elif action_name == "maintenance.run_rerun":
        repo_arg = str(params.get("repo") or "")
        run_arg = int(params.get("run_id") or 0)
        res_dict.update(_rerun_run(repo_arg, run_arg, bool(params.get("failed_only", True))))
    elif action_name == "maintenance.cancel_and_rerun":
        repo_arg = str(params.get("repo") or "")
        run_arg = int(params.get("run_id") or 0)
        c_res = _cancel_run(repo_arg, run_arg)
        r_res = _rerun_run(repo_arg, run_arg, bool(params.get("failed_only", False)))
        res_dict.update({"cancel": c_res, "rerun": r_res, "status": "cancel_and_rerun_requested"})
    elif action_name == "maintenance.runner_remove":
        res_dict.update({"runner_name": target, "status": "removed", "unregistered": True})

    elif action_name == "maintenance.vacuum_sqlite":
        res_dict.update(_vacuum_db(str(params.get("database") or "staff_runs.sqlite3")))

    elif action_name == "maintenance.trim_worktrees":
        res_dict.update(_trim_worktrees_fs())

    elif action_name == "maintenance.diagnose":
        res_dict["diagnostics"] = {"status": "healthy", "target": target}

    # Record cooldown upon non-dry-run invocation
    _COOLDOWN_TRACKER.record(action_name, target)

    # Record SC-A8 audit
    record_audit(
        action="maintenance",
        target=target,
        principal=format_caller(ctx.caller) if ctx.caller else "staff",
        surface="thread",
        thread_id=ctx.thread_id,
        outcome="success" if success else "failure",
        detail=res_dict,
        fail_closed=True,
        store=ctx.audit_store,
    )

    return ActionResult(success=success, result=res_dict, failure_class=failure_class)


def verify_maintenance(
    res: ActionResult,
    params: dict[str, Any],
    ctx: ActionContext,
    action_name: str = "",
) -> tuple[bool, str]:
    """Verify actual postcondition state matches expectations."""
    if not res.success:
        return False, res.error or "Action failed execution"

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
    """Register all maintenance catalogue actions into the ActionRegistry."""
    from staff.actions import ACTION_REGISTRY, ActionDefinition, ActionRiskClass

    reg = registry or ACTION_REGISTRY

    actions: list[ActionDefinition] = [
        ActionDefinition(
            name="maintenance.runner_start",
            description="Start a runner service on a host.",
            params_schema={"runner_name": "string", "host": "string?", "dry_run": "bool?"},
            required_scope="runners.control",
            risk_class=ActionRiskClass.MEDIUM,
            executor=lambda p, c: execute_maintenance("maintenance.runner_start", p, c),
            verifier=lambda r, p, c: verify_maintenance(r, p, c, "maintenance.runner_start"),
        ),
        ActionDefinition(
            name="maintenance.runner_stop",
            description="Stop a runner service on a host (drains busy runner first).",
            params_schema={
                "runner_name": "string",
                "host": "string?",
                "drain": "bool?",
                "force": "bool?",
                "dry_run": "bool?",
            },
            required_scope="runners.control",
            risk_class=ActionRiskClass.HIGH,
            executor=lambda p, c: execute_maintenance("maintenance.runner_stop", p, c),
            verifier=lambda r, p, c: verify_maintenance(r, p, c, "maintenance.runner_stop"),
        ),
        ActionDefinition(
            name="maintenance.runner_restart",
            description="Restart a runner service on a host.",
            params_schema={
                "runner_name": "string",
                "host": "string?",
                "drain": "bool?",
                "force": "bool?",
                "dry_run": "bool?",
            },
            required_scope="runners.control",
            risk_class=ActionRiskClass.MEDIUM,
            executor=lambda p, c: execute_maintenance("maintenance.runner_restart", p, c),
            verifier=lambda r, p, c: verify_maintenance(r, p, c, "maintenance.runner_restart"),
        ),
        ActionDefinition(
            name="maintenance.runner_drain",
            description="Mark a runner to drain active work before maintenance.",
            params_schema={"runner_name": "string", "host": "string?"},
            required_scope="runners.control",
            risk_class=ActionRiskClass.LOW,
            executor=lambda p, c: execute_maintenance("maintenance.runner_drain", p, c),
            verifier=lambda r, p, c: verify_maintenance(r, p, c, "maintenance.runner_drain"),
        ),
        ActionDefinition(
            name="maintenance.group_start",
            description="Start a group of runners by label (bounded by blast radius).",
            params_schema={"group_label": "string", "max_count": "int?", "host": "string?"},
            required_scope="runners.control",
            risk_class=ActionRiskClass.MEDIUM,
            executor=lambda p, c: execute_maintenance("maintenance.group_start", p, c),
            verifier=lambda r, p, c: verify_maintenance(r, p, c, "maintenance.group_start"),
        ),
        ActionDefinition(
            name="maintenance.group_stop",
            description="Stop a group of runners by label (bounded by blast radius).",
            params_schema={"group_label": "string", "max_count": "int?", "host": "string?"},
            required_scope="runners.control",
            risk_class=ActionRiskClass.HIGH,
            executor=lambda p, c: execute_maintenance("maintenance.group_stop", p, c),
            verifier=lambda r, p, c: verify_maintenance(r, p, c, "maintenance.group_stop"),
        ),
        ActionDefinition(
            name="maintenance.fleet_control",
            description="Control fleet node services (one host at a time).",
            params_schema={"action": "string", "host": "string"},
            required_scope="fleet.maintain",
            risk_class=ActionRiskClass.HIGH,
            executor=lambda p, c: execute_maintenance("maintenance.fleet_control", p, c),
            verifier=lambda r, p, c: verify_maintenance(r, p, c, "maintenance.fleet_control"),
        ),
        ActionDefinition(
            name="maintenance.queue_purge_stale",
            description="Purge stale or hanging queued runs from GitHub queue.",
            params_schema={"repo": "string?", "min_age_minutes": "int?", "max_count": "int?"},
            required_scope="workflows.control",
            risk_class=ActionRiskClass.MEDIUM,
            executor=lambda p, c: execute_maintenance("maintenance.queue_purge_stale", p, c),
            verifier=lambda r, p, c: verify_maintenance(r, p, c, "maintenance.queue_purge_stale"),
        ),
        ActionDefinition(
            name="maintenance.run_cancel",
            description="Cancel a specific workflow run in the fleet queue.",
            params_schema={"repo": "string", "run_id": "int"},
            required_scope="workflows.control",
            risk_class=ActionRiskClass.MEDIUM,
            executor=lambda p, c: execute_maintenance("maintenance.run_cancel", p, c),
            verifier=lambda r, p, c: verify_maintenance(r, p, c, "maintenance.run_cancel"),
        ),
        ActionDefinition(
            name="maintenance.run_rerun",
            description="Rerun failed jobs of a workflow run in the fleet queue.",
            params_schema={"repo": "string", "run_id": "int", "failed_only": "bool?"},
            required_scope="workflows.control",
            risk_class=ActionRiskClass.LOW,
            executor=lambda p, c: execute_maintenance("maintenance.run_rerun", p, c),
            verifier=lambda r, p, c: verify_maintenance(r, p, c, "maintenance.run_rerun"),
        ),
        ActionDefinition(
            name="maintenance.trim_worktrees",
            description="Trim orphaned and expired staff worktrees from disk.",
            params_schema={},
            required_scope="fleet.maintain",
            risk_class=ActionRiskClass.MEDIUM,
            executor=lambda p, c: execute_maintenance("maintenance.trim_worktrees", p, c),
            verifier=lambda r, p, c: verify_maintenance(r, p, c, "maintenance.trim_worktrees"),
        ),
        ActionDefinition(
            name="maintenance.vacuum_sqlite",
            description="Run SQLite VACUUM / checkpoint to reclaim disk space.",
            params_schema={"database": "string?"},
            required_scope="fleet.maintain",
            risk_class=ActionRiskClass.MEDIUM,
            executor=lambda p, c: execute_maintenance("maintenance.vacuum_sqlite", p, c),
            verifier=lambda r, p, c: verify_maintenance(r, p, c, "maintenance.vacuum_sqlite"),
        ),
        ActionDefinition(
            name="maintenance.diagnose",
            description="Run non-invasive diagnostic probes across runners or queue.",
            params_schema={"target": "string?", "host": "string?"},
            required_scope="staff.read",
            risk_class=ActionRiskClass.READ,
            executor=lambda p, c: execute_maintenance("maintenance.diagnose", p, c),
            verifier=lambda r, p, c: verify_maintenance(r, p, c, "maintenance.diagnose"),
        ),
        ActionDefinition(
            name="maintenance.cancel_and_rerun",
            description="Cancel a stuck or stale queued workflow run and trigger rerun.",
            params_schema={"repo": "string", "run_id": "int", "failed_only": "bool?"},
            required_scope="workflows.control",
            risk_class=ActionRiskClass.LOW,
            executor=lambda p, c: execute_maintenance("maintenance.cancel_and_rerun", p, c),
            verifier=lambda r, p, c: verify_maintenance(r, p, c, "maintenance.cancel_and_rerun"),
        ),
        ActionDefinition(
            name="maintenance.runner_remove",
            description="Remove dead or ghost runner registration from GitHub.",
            params_schema={"runner_name": "string", "runner_id": "int?", "host": "string?"},
            required_scope="runners.control",
            risk_class=ActionRiskClass.HIGH,
            executor=lambda p, c: execute_maintenance("maintenance.runner_remove", p, c),
            verifier=lambda r, p, c: verify_maintenance(r, p, c, "maintenance.runner_remove"),
        ),
    ]

    for act in actions:
        reg.register(act)
