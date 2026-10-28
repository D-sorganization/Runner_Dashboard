"""Validate builder roles against the locked grant ceiling."""

from __future__ import annotations

from dataclasses import dataclass

from staff.bot_builder.grant_ceiling import GrantCeiling
from staff.bot_builder.schema import BuilderRoleDefinition


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reasons: tuple[str, ...]


def validate_builder_role(role: BuilderRoleDefinition, ceiling: GrantCeiling) -> ValidationResult:
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

    return ValidationResult(ok=not reasons, reasons=tuple(reasons))
