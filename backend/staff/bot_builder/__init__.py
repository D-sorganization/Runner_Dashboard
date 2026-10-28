"""Bot-builder validator and escalation diff (RD #3103, slice 2)."""

from staff.bot_builder.escalation import EscalationDiff, escalation_diff
from staff.bot_builder.grant_ceiling import GrantCeiling, GrantCeilingError, load_grant_ceiling
from staff.bot_builder.schema import (
    BUILDER_EDITOR_V1,
    BuilderRoleDefinition,
    TriggerEdge,
    parse_builder_role,
)
from staff.bot_builder.validator import ValidationResult, validate_builder_role

__all__ = [
    "BUILDER_EDITOR_V1",
    "BuilderRoleDefinition",
    "EscalationDiff",
    "GrantCeiling",
    "GrantCeilingError",
    "TriggerEdge",
    "ValidationResult",
    "escalation_diff",
    "load_grant_ceiling",
    "parse_builder_role",
    "validate_builder_role",
]
