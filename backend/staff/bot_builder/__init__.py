"""Bot-builder role schema and locked grant ceiling (RD #3103, slice 1)."""

from staff.bot_builder.grant_ceiling import GrantCeiling, GrantCeilingError, load_grant_ceiling
from staff.bot_builder.schema import (
    BUILDER_EDITOR_V1,
    BuilderRoleDefinition,
    TriggerEdge,
    parse_builder_role,
)

__all__ = [
    "BUILDER_EDITOR_V1",
    "BuilderRoleDefinition",
    "GrantCeiling",
    "GrantCeilingError",
    "TriggerEdge",
    "load_grant_ceiling",
    "parse_builder_role",
]
