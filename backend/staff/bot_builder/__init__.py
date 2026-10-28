"""Bot-builder validator, escalation diff, and draft/publish/version API (RD #3103, #3104)."""

from staff.bot_builder.escalation import EscalationDiff, escalation_diff
from staff.bot_builder.grant_ceiling import GrantCeiling, GrantCeilingError, load_grant_ceiling
from staff.bot_builder.publish import (
    Draft,
    DraftMode,
    PreviewResult,
    PublishError,
    create_draft,
    preview_draft,
    publish_draft,
    rollback_publish,
    validate_draft,
)
from staff.bot_builder.schema import (
    BUILDER_EDITOR_V1,
    BuilderRoleDefinition,
    TriggerEdge,
    parse_builder_role,
)
from staff.bot_builder.validator import ValidationResult, validate_builder_role
from staff.bot_builder.versions import RoleVersion, RoleVersionLedger, compute_content_hash, dispatch_task

__all__ = [
    "BUILDER_EDITOR_V1",
    "BuilderRoleDefinition",
    "Draft",
    "DraftMode",
    "EscalationDiff",
    "GrantCeiling",
    "GrantCeilingError",
    "PreviewResult",
    "PublishError",
    "RoleVersion",
    "RoleVersionLedger",
    "TriggerEdge",
    "ValidationResult",
    "compute_content_hash",
    "create_draft",
    "dispatch_task",
    "escalation_diff",
    "load_grant_ceiling",
    "parse_builder_role",
    "preview_draft",
    "publish_draft",
    "rollback_publish",
    "validate_builder_role",
    "validate_draft",
]
