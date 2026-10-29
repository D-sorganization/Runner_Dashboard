"""Bot-builder validator, escalation diff, publish/version API, and activation gate (RD #3103-#3105)."""

from staff.bot_builder.activation import (
    ALL_MUTATING_SURFACES,
    ActivationError,
    ApprovalRecord,
    DryRunRecord,
    DryRunRecordedAction,
    MutatingSurface,
    ShadowDryRunSession,
    ShadowModeMutationRefused,
    approve_activation,
    is_material_change,
    material_change_fields,
)
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
    "ALL_MUTATING_SURFACES",
    "BUILDER_EDITOR_V1",
    "ActivationError",
    "ApprovalRecord",
    "BuilderRoleDefinition",
    "Draft",
    "DraftMode",
    "DryRunRecord",
    "DryRunRecordedAction",
    "EscalationDiff",
    "GrantCeiling",
    "GrantCeilingError",
    "MutatingSurface",
    "PreviewResult",
    "PublishError",
    "RoleVersion",
    "RoleVersionLedger",
    "ShadowDryRunSession",
    "ShadowModeMutationRefused",
    "TriggerEdge",
    "ValidationResult",
    "approve_activation",
    "compute_content_hash",
    "create_draft",
    "dispatch_task",
    "escalation_diff",
    "is_material_change",
    "load_grant_ceiling",
    "material_change_fields",
    "parse_builder_role",
    "preview_draft",
    "publish_draft",
    "rollback_publish",
    "validate_builder_role",
    "validate_draft",
]
