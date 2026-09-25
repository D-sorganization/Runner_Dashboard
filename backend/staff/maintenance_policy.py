"""Maintenance policy table (SC-E7, Issue #1344).

`MAINTENANCE_POLICY` is the single source of every maintenance action's risk,
scope and limits. `staff.maintenance` registers actions from it and runs
`check_maintenance_policy` before every execution;
tests/staff/test_maintenance_safety.py pins it with a mutation check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_BATCH_RUNNERS = 10
MAX_QUEUE_PURGE = 50
DEFAULT_QUEUE_PURGE = 20
# Host or target values that mean "everywhere"; disruptive actions refuse them.
FLEET_WIDE_HOSTS = frozenset({"all", "*", "fleet"})


class MaintenanceError(Exception):
    """Base error for maintenance operations."""


class MaintenancePreconditionError(MaintenanceError):
    """Raised when safety preconditions are violated."""


@dataclass(frozen=True)
class MaintenancePolicy:
    """Safety contract for one maintenance action.

    disruptive:   refuses a fleet-wide host (never all hosts at once).
    target_param: parameter that must name exactly one target (never the fleet).
    max_targets:  blast-radius cap on the requested `max_count`.
    """

    description: str
    risk_class: str
    required_scope: str
    params_schema: dict[str, str] = field(default_factory=dict)
    disruptive: bool = False
    target_param: str | None = None
    max_targets: int | None = None


_RUNNER_PARAMS = {"runner_name": "string", "host": "string?", "drain": "bool?", "force": "bool?", "dry_run": "bool?"}
_GROUP_PARAMS = {"group_label": "string", "max_count": "int?", "host": "string?"}
_RUN_PARAMS = {"repo": "string", "run_id": "int", "failed_only": "bool?"}

MAINTENANCE_POLICY: dict[str, MaintenancePolicy] = {
    "maintenance.runner_start": MaintenancePolicy(
        "Start a runner service on a host.",
        "medium",
        "runners.control",
        {"runner_name": "string", "host": "string?", "dry_run": "bool?"},
        disruptive=True,
        target_param="runner_name",
    ),
    "maintenance.runner_stop": MaintenancePolicy(
        "Stop a runner service on a host (drains busy runner first).",
        "high",
        "runners.control",
        _RUNNER_PARAMS,
        disruptive=True,
        target_param="runner_name",
    ),
    "maintenance.runner_restart": MaintenancePolicy(
        "Restart a runner service on a host.",
        "medium",
        "runners.control",
        _RUNNER_PARAMS,
        disruptive=True,
        target_param="runner_name",
    ),
    "maintenance.runner_drain": MaintenancePolicy(
        "Mark a runner to drain active work before maintenance.",
        "low",
        "runners.control",
        {"runner_name": "string", "host": "string?"},
        disruptive=True,
        target_param="runner_name",
    ),
    "maintenance.group_start": MaintenancePolicy(
        "Start a group of runners by label (bounded by blast radius).",
        "medium",
        "runners.control",
        _GROUP_PARAMS,
        disruptive=True,
        target_param="group_label",
        max_targets=MAX_BATCH_RUNNERS,
    ),
    "maintenance.group_stop": MaintenancePolicy(
        "Stop a group of runners by label (bounded by blast radius).",
        "high",
        "runners.control",
        _GROUP_PARAMS,
        disruptive=True,
        target_param="group_label",
        max_targets=MAX_BATCH_RUNNERS,
    ),
    "maintenance.fleet_control": MaintenancePolicy(
        "Control fleet node services (one host at a time).",
        "high",
        "fleet.maintain",
        {"action": "string", "host": "string"},
        disruptive=True,
        target_param="host",
    ),
    "maintenance.runner_remove": MaintenancePolicy(
        "Remove dead or ghost runner registration from GitHub.",
        "high",
        "runners.control",
        {"runner_name": "string", "runner_id": "int?", "host": "string?"},
        disruptive=True,
        target_param="runner_name",
    ),
    "maintenance.queue_purge_stale": MaintenancePolicy(
        "Purge stale or hanging queued runs from GitHub queue.",
        "medium",
        "workflows.control",
        {"repo": "string?", "min_age_minutes": "int?", "max_count": "int?"},
        max_targets=MAX_QUEUE_PURGE,
    ),
    "maintenance.run_cancel": MaintenancePolicy(
        "Cancel a specific workflow run in the fleet queue.",
        "medium",
        "workflows.control",
        {"repo": "string", "run_id": "int"},
        target_param="run_id",
    ),
    "maintenance.run_rerun": MaintenancePolicy(
        "Rerun failed jobs of a workflow run in the fleet queue.",
        "low",
        "workflows.control",
        _RUN_PARAMS,
        target_param="run_id",
    ),
    "maintenance.cancel_and_rerun": MaintenancePolicy(
        "Cancel a stuck or stale queued workflow run and trigger rerun.",
        "low",
        "workflows.control",
        _RUN_PARAMS,
        target_param="run_id",
    ),
    "maintenance.trim_worktrees": MaintenancePolicy(
        "Trim orphaned and expired staff worktrees from disk.",
        "medium",
        "fleet.maintain",
    ),
    "maintenance.vacuum_sqlite": MaintenancePolicy(
        "Run SQLite VACUUM / checkpoint to reclaim disk space.",
        "medium",
        "fleet.maintain",
        {"database": "string?"},
    ),
    "maintenance.diagnose": MaintenancePolicy(
        "Run non-invasive diagnostic probes across runners or queue.",
        "read",
        "staff.read",
        {"target": "string?", "host": "string?"},
    ),
}


def requested_count(action_name: str, params: dict[str, Any], policy: MaintenancePolicy) -> int:
    default = DEFAULT_QUEUE_PURGE if action_name == "maintenance.queue_purge_stale" else policy.max_targets
    return int(params.get("max_count") or default or 0)


def check_maintenance_policy(action_name: str, params: dict[str, Any]) -> MaintenancePolicy:
    """Preflight gates read from MAINTENANCE_POLICY; raises MaintenancePreconditionError on any breach."""
    policy = MAINTENANCE_POLICY.get(action_name)
    if policy is None:
        raise MaintenancePreconditionError(f"'{action_name}' is not in the maintenance policy table")

    host = str(params.get("host") or "local").strip().lower()
    if policy.disruptive and host in FLEET_WIDE_HOSTS:
        raise MaintenancePreconditionError(
            f"One host at a time: '{action_name}' cannot target host '{host}'. Host 'all' not permitted."
        )

    if policy.target_param:
        value = params.get(policy.target_param)
        if value is None or str(value).strip() in ("", "0"):
            raise MaintenancePreconditionError(f"'{action_name}' requires an explicit {policy.target_param}")
        if str(value).strip().lower() in FLEET_WIDE_HOSTS:
            raise MaintenancePreconditionError(
                f"'{action_name}' takes a single target; {policy.target_param}='{value}' names the whole fleet"
            )

    if policy.max_targets is not None:
        count = requested_count(action_name, params, policy)
        if count > policy.max_targets:
            raise MaintenancePreconditionError(
                f"Batch count {count} exceeds safety blast-radius limit of {policy.max_targets}"
            )
    return policy
