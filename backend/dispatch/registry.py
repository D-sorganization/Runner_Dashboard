"""Dispatch action registry — allowlisted actions and access control.

Public API:
- DispatchAccess (enum)
- DispatchAction (dataclass)
- ALLOWLISTED_ACTIONS (dict)
- get_action(action_name) -> DispatchAction | None
- requires_confirmation(action_name) -> bool
- _scheduler_modify_command(payload) -> tuple[str, ...]
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any


class _StrEnum(str, enum.Enum):  # noqa: UP042
    """Python 3.10 compatible StrEnum."""

    pass


class DispatchAccess(_StrEnum):
    """Access level for an allowlisted dispatch action."""

    READ_ONLY = "read_only"
    PRIVILEGED = "privileged"


@dataclass(frozen=True, slots=True)
class DispatchAction:
    """Allowlisted action definition."""

    name: str
    access: DispatchAccess
    description: str
    prototype_command: tuple[str, ...]
    requires_confirmation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "access": self.access.value,
            "description": self.description,
            "prototype_command": list(self.prototype_command),
            "requires_confirmation": self.requires_confirmation,
        }


ALLOWLISTED_ACTIONS: dict[str, DispatchAction] = {
    # ── Read-only actions ────────────────────────────────────────────────────
    "dashboard.status": DispatchAction(
        name="dashboard.status",
        access=DispatchAccess.READ_ONLY,
        description="Read-only dashboard health and status snapshot.",
        prototype_command=("python3", "-m", "json.tool"),
    ),
    "runner.status": DispatchAction(
        name="runner.status",
        access=DispatchAccess.READ_ONLY,
        description="List runner services and their systemd status on the node.",
        prototype_command=("systemctl", "status", "actions.runner.*"),
    ),
    "scheduler.list": DispatchAction(
        name="scheduler.list",
        access=DispatchAccess.READ_ONLY,
        description="List scheduled maintenance jobs known to the node.",
        prototype_command=("systemctl", "list-timers", "--all"),
    ),
    # ── Privileged actions (require explicit confirmation) ────────────────────
    "dashboard.update_and_restart": DispatchAction(
        name="dashboard.update_and_restart",
        access=DispatchAccess.PRIVILEGED,
        description="Apply the deployed dashboard update helper and restart.",
        prototype_command=("bash", "runner-dashboard/deploy/update-deployed.sh"),
        requires_confirmation=True,
    ),
    "runner.restart": DispatchAction(
        name="runner.restart",
        access=DispatchAccess.PRIVILEGED,
        description="Restart one or all GitHub Actions runner services on the node.",
        prototype_command=("sudo", "systemctl", "restart", "actions.runner.*"),
        requires_confirmation=True,
    ),
    "runner.stop": DispatchAction(
        name="runner.stop",
        access=DispatchAccess.PRIVILEGED,
        description=("Stop one or all GitHub Actions runner services. Destructive: in-flight jobs will be abandoned."),
        prototype_command=("sudo", "systemctl", "stop", "actions.runner.*"),
        requires_confirmation=True,
    ),
    "service.unregister": DispatchAction(
        name="service.unregister",
        access=DispatchAccess.PRIVILEGED,
        description=(
            "Remove a runner or service registration from the node. "
            "Destructive: cannot be undone without re-registration."
        ),
        prototype_command=("sudo", "systemctl", "disable", "--now"),
        requires_confirmation=True,
    ),
    "scheduler.modify": DispatchAction(
        name="scheduler.modify",
        access=DispatchAccess.PRIVILEGED,
        description=("Enable or disable a scheduled maintenance job. Affects recurring fleet maintenance windows."),
        prototype_command=("sudo", "systemctl", "enable|disable", "<unit>"),
        requires_confirmation=True,
    ),
    # ── Agent dispatch actions ────────────────────────────────────────────────
    "agents.dispatch.adhoc": DispatchAction(
        name="agents.dispatch.adhoc",
        access=DispatchAccess.PRIVILEGED,
        description="Dispatch an agent for an ad-hoc task via the quick-dispatch workflow.",
        prototype_command=("gh", "workflow", "run", "Agent-Quick-Dispatch.yml"),
        requires_confirmation=True,
    ),
    "agents.dispatch.pr": DispatchAction(
        name="agents.dispatch.pr",
        access=DispatchAccess.PRIVILEGED,
        description="Dispatch agents to one or more pull requests via the Agent-PR-Action workflow.",
        prototype_command=("gh", "workflow", "run", "Agent-PR-Action.yml"),
        requires_confirmation=True,
    ),
    "agents.dispatch.issue": DispatchAction(
        name="agents.dispatch.issue",
        access=DispatchAccess.PRIVILEGED,
        description="Dispatch agents to one or more issues via the Agent-Issue-Action workflow.",
        prototype_command=("gh", "workflow", "run", "Agent-Issue-Action.yml"),
        requires_confirmation=True,
    ),
}


def _scheduler_modify_command(payload: dict[str, Any]) -> tuple[str, ...]:
    raw_mode = payload.get("mode")
    if raw_mode is None and "enabled" in payload:
        raw_mode = "enable" if payload["enabled"] else "disable"
    mode = str(raw_mode or "").strip().lower()
    if mode in {"enable", "enabled", "on", "true"}:
        systemctl_mode = "enable"
    elif mode in {"disable", "disabled", "off", "false"}:
        systemctl_mode = "disable"
    else:
        raise ValueError("scheduler.modify payload must request enable or disable")

    unit = str(
        payload.get("unit") or payload.get("timer") or payload.get("service") or "runner-scheduler.timer"
    ).strip()
    if not unit:
        raise ValueError("scheduler.modify payload must include a systemd unit")
    return ("sudo", "systemctl", systemctl_mode, unit)


def get_action(action_name: str) -> DispatchAction | None:
    return ALLOWLISTED_ACTIONS.get(action_name)


def requires_confirmation(action_name: str) -> bool:
    action = get_action(action_name)
    return bool(action and action.requires_confirmation)
