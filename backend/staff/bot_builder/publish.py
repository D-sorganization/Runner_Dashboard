"""Draft/validate/preview/publish API for template/scratch/clone/edit (RD #3104, #3105, #3107).

One draft flow serves every mode; clone always re-validates fresh against
the current ceiling, never inheriting a prior pass. Publish/rollback reuse
``validate_builder_role`` and ``escalation_diff`` and log to the shared RD
audit log. New bots and materially changed bots start disabled until a
supervised dry run is approved by Dieter; non-material edits preserve
existing activation.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from orchestration_audit import append_orchestration_audit
from staff.bot_builder.activation import is_material_change
from staff.bot_builder.escalation import EscalationDiff, escalation_diff
from staff.bot_builder.grant_ceiling import GrantCeiling
from staff.bot_builder.schedules import DEFAULT_SCHEDULE_POLICY, SchedulePolicy
from staff.bot_builder.schema import BuilderRoleDefinition, TriggerEdge, parse_builder_role
from staff.bot_builder.validator import ValidationResult, validate_builder_role
from staff.bot_builder.versions import RoleVersion, RoleVersionLedger


class DraftMode(enum.Enum):
    TEMPLATE = "template"
    SCRATCH = "scratch"
    CLONE = "clone"
    EDIT = "edit"


class PublishError(RuntimeError):
    """Publish or rollback was refused; message carries non-sensitive reasons only."""


@dataclass(frozen=True)
class Draft:
    mode: DraftMode
    role: BuilderRoleDefinition
    baseline_previous: BuilderRoleDefinition | None = None
    baseline_original: BuilderRoleDefinition | None = None
    source_version_id: str | None = None


@dataclass(frozen=True)
class PreviewResult:
    validation: ValidationResult
    diff: EscalationDiff | None
    host_effects: dict[str, object]
    export_effects: dict[str, object]


def create_draft(
    mode: DraftMode,
    data: dict[str, object],
    *,
    ledger: RoleVersionLedger | None = None,
    source: RoleVersion | None = None,
) -> Draft:
    """DbC: ``data`` is a full role payload for every mode; clone/edit always revalidate fresh."""
    parsed = parse_builder_role(data)

    if mode is DraftMode.EDIT:
        if ledger is None or ledger.current is None:
            raise ValueError("edit mode requires a ledger with an existing published version")
        keep_active = ledger.is_active and not is_material_change(ledger.current.role, parsed)
        role = dataclasses.replace(parsed, enabled=keep_active)
        return Draft(mode, role, baseline_previous=ledger.current.role, baseline_original=ledger.original_role)

    role = dataclasses.replace(parsed, enabled=False)
    if mode is DraftMode.CLONE:
        if source is None:
            raise ValueError("clone mode requires a source version")
        return Draft(
            mode,
            role,
            baseline_previous=source.role,
            baseline_original=source.role,
            source_version_id=source.version_id,
        )

    return Draft(mode, role)


def validate_draft(
    draft: Draft,
    ceiling: GrantCeiling,
    *,
    schedule_policy: SchedulePolicy = DEFAULT_SCHEDULE_POLICY,
    declared_edges_by_role: Mapping[str, Sequence[TriggerEdge]] | None = None,
) -> ValidationResult:
    """Call the one shared validator — never reimplement its rules here."""
    return validate_builder_role(
        draft.role,
        ceiling,
        schedule_policy=schedule_policy,
        declared_edges_by_role=declared_edges_by_role,
    )


def preview_draft(draft: Draft, ceiling: GrantCeiling) -> PreviewResult:
    """Exact diff, policy findings, and host/export effects for a draft."""
    validation = validate_builder_role(draft.role, ceiling)
    diff: EscalationDiff | None = None
    if draft.baseline_previous is not None and draft.baseline_original is not None:
        diff = escalation_diff(draft.baseline_original, draft.baseline_previous, draft.role)
    host_changed = draft.baseline_previous is not None and draft.baseline_previous.host != draft.role.host
    host_effects: dict[str, object] = {"host": draft.role.host, "changed": host_changed}
    export_effects: dict[str, object] = {"capabilities": draft.role.capabilities, "providers": draft.role.providers}
    return PreviewResult(validation, diff, host_effects, export_effects)


def _requires_confirmation(ledger: RoleVersionLedger, role: BuilderRoleDefinition) -> tuple[str, ...]:
    previous = ledger.current
    if previous is None:
        return ()
    original = ledger.original_role or previous.role
    diff = escalation_diff(original, previous.role, role)
    reasons = list(diff.requires_confirmation)
    if diff.newly_able_vs_previous:
        reasons.append("widening")
    return tuple(reasons)


async def publish_draft(
    draft: Draft,
    ceiling: GrantCeiling,
    ledger: RoleVersionLedger,
    *,
    confirmed: bool,
    principal: str,
    schedule_policy: SchedulePolicy = DEFAULT_SCHEDULE_POLICY,
    declared_edges_by_role: Mapping[str, Sequence[TriggerEdge]] | None = None,
) -> RoleVersion:
    """DbC: draft.role belongs to ``ledger``'s lineage; blocks widening/cycles/self-widening."""
    validation = validate_builder_role(
        draft.role,
        ceiling,
        schedule_policy=schedule_policy,
        declared_edges_by_role=declared_edges_by_role,
    )
    if not validation.ok:
        raise PublishError(f"publish blocked: validation failed ({'; '.join(validation.reasons)})")

    reasons = _requires_confirmation(ledger, draft.role)
    if reasons and principal.strip() == draft.role.name:
        raise PublishError("publish blocked: self-widening of role bounds is forbidden")
    if reasons and not confirmed:
        raise PublishError(
            "publish blocked without confirmation: " + ", ".join(sorted(set(reasons))),
        )

    previous = ledger.current
    was_active = ledger.is_active
    material = previous is None or is_material_change(previous.role, draft.role)
    role_to_publish = dataclasses.replace(draft.role, enabled=(False if material else was_active))
    version = ledger.publish(role_to_publish)
    if material:
        ledger.revoke_activation()
    else:
        ledger.enable_activation_gate()

    await append_orchestration_audit(
        {
            "orchestration_type": "bot_builder",
            "action": "bot_builder.publish",
            "role_id": ledger.role_id,
            "mode": draft.mode.value,
            "version_id": version.version_id,
            "content_hash": version.content_hash,
            "confirmed": confirmed,
            "confirmation_reasons": sorted(set(reasons)),
            "principal": principal,
        },
    )
    return version


async def rollback_publish(
    ledger: RoleVersionLedger,
    target_version_id: str,
    ceiling: GrantCeiling,
    *,
    principal: str,
) -> RoleVersion:
    """Publish a new version equal to ``target_version_id``; history stays immutable."""
    target = ledger.get(target_version_id)
    validation = validate_builder_role(target.role, ceiling)
    if not validation.ok:
        raise PublishError(
            f"rollback blocked: target version fails current ceiling ({'; '.join(validation.reasons)})",
        )

    previous = ledger.current
    version = ledger.rollback(target_version_id)
    if previous is None or is_material_change(previous.role, target.role):
        ledger.revoke_activation()
    await append_orchestration_audit(
        {
            "orchestration_type": "bot_builder",
            "action": "bot_builder.rollback",
            "role_id": ledger.role_id,
            "version_id": version.version_id,
            "content_hash": version.content_hash,
            "rolled_back_from": version.rolled_back_from,
            "principal": principal,
        },
    )
    return version
