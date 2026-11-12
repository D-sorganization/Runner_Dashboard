"""Validate builder roles against the locked grant ceiling and schedule/trigger policy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from staff.bot_builder.grant_ceiling import GrantCeiling
from staff.bot_builder.schedules import (
    DEFAULT_SCHEDULE_POLICY,
    SchedulePolicy,
    find_trigger_cycle_reasons,
    validate_schedule_bounds,
)
from staff.bot_builder.schema import BuilderRoleDefinition, TriggerEdge


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reasons: tuple[str, ...]


def validate_trigger_graph(
    role_name: str,
    candidate_triggers: Sequence[TriggerEdge],
    declared_edges_by_role: Mapping[str, Sequence[TriggerEdge]],
) -> ValidationResult:
    """Validate cross-role trigger graph using declared trigger edges only."""
    reasons = find_trigger_cycle_reasons(role_name, candidate_triggers, declared_edges_by_role)
    return ValidationResult(ok=not reasons, reasons=reasons)


def validate_builder_role(
    role: BuilderRoleDefinition,
    ceiling: GrantCeiling,
    *,
    schedule_policy: SchedulePolicy = DEFAULT_SCHEDULE_POLICY,
    declared_edges_by_role: Mapping[str, Sequence[TriggerEdge]] | None = None,
) -> ValidationResult:
    """DbC: role and ceiling must be parsed; returns non-sensitive refusal reasons."""
    if role.schema_version != 1:
        return ValidationResult(ok=False, reasons=("unsupported schema version",))

    reasons: list[str] = []

    for provider in role.providers:
        if provider not in ceiling.approved_providers:
            reasons.append("provider not on approved list: category provider")

    for cap in role.capabilities:
        if cap.startswith("hold."):
            reasons.append("hold capabilities are not grantable")
            continue
        if cap in ceiling.household_capabilities:
            if not role.household_data_opt_in:
                reasons.append("household capability requires explicit opt-in")
            continue
        if cap not in ceiling.allowed_capabilities:
            reasons.append("capability above grant ceiling: category capability")

    reasons.extend(validate_schedule_bounds(role, schedule_policy))
    if declared_edges_by_role is not None:
        reasons.extend(find_trigger_cycle_reasons(role.name, role.triggers, declared_edges_by_role))

    return ValidationResult(ok=not reasons, reasons=tuple(reasons))
