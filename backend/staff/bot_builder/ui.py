"""Form-first bot-builder UI view-model, standalone approval screen, and workspace flow (RD #3106)."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from staff.bot_builder.activation import ApprovalRecord, DryRunRecord, DryRunRecordedAction, approve_activation
from staff.bot_builder.escalation import escalation_diff
from staff.bot_builder.grant_ceiling import GrantCeiling
from staff.bot_builder.publish import (
    Draft,
    DraftMode,
    create_draft,
    preview_draft,
    publish_draft,
    rollback_publish,
    validate_draft,
)
from staff.bot_builder.schedules import DEFAULT_SCHEDULE_POLICY, SchedulePolicy
from staff.bot_builder.schema import BUILDER_EDITOR_V1, BuilderRoleDefinition, ScheduleBounds, TriggerEdge
from staff.bot_builder.validator import ValidationResult
from staff.bot_builder.versions import RoleVersion, RoleVersionLedger

_CEILING_KEYS: frozenset[str] = frozenset(
    {"approved_providers", "allowed_capabilities", "household_capabilities", "grant_ceiling", "ceiling"}
)
_HOLD_KEYS: frozenset[str] = frozenset(
    {"hold", "holds", "hold_id", "hold_ids", "lift_hold", "safety_limits", "safety_limit", "grant_hold"}
)


class BuilderUiError(RuntimeError):
    """Form-builder UI boundary refusal (non-sensitive category reason only)."""


def open_editor_view(view_mode: str = BUILDER_EDITOR_V1) -> str:
    """DbC: v1 ships form-only; YAML or advanced views are refused at the boundary."""
    if not isinstance(view_mode, str) or view_mode.strip().lower() != BUILDER_EDITOR_V1:
        raise BuilderUiError("v1 builder is form-only; YAML/advanced editor is deferred")
    return BUILDER_EDITOR_V1


@dataclass(frozen=True)
class BuilderFormValues:
    name: str
    persona: str
    instructions: str
    skills: tuple[str, ...] = ()
    model: str | None = None
    providers: tuple[str, ...] = ("sub-cli-alpha",)
    schedule: str | None = None
    triggers: tuple[TriggerEdge, ...] = ()
    host: str = "grokbot-first"
    capabilities: tuple[str, ...] = ()
    private_context_ref: str | None = None
    household_data_opt_in: bool = False
    description: str = ""
    schedule_bounds: ScheduleBounds | None = None

    @classmethod
    def from_template(cls, tpl: BuilderTemplate, *, name: str) -> BuilderFormValues:
        return dataclasses.replace(tpl.defaults, name=name)

    @classmethod
    def from_version(cls, version: RoleVersion, **overrides: object) -> BuilderFormValues:
        fields = {f.name: getattr(version.role, f.name) for f in dataclasses.fields(cls)}
        fields.update(overrides)
        return cls(**fields)  # type: ignore[arg-type]

    def to_payload(self) -> dict[str, object]:
        d: dict[str, object] = {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}
        edges = [{"target_role": t.target_role, "event": t.event} for t in self.triggers]
        d.update(schema_version=1, enabled=False, skills=list(self.skills), providers=list(self.providers))
        d.update(capabilities=list(self.capabilities), triggers=edges)
        return d


@dataclass(frozen=True)
class BuilderTemplate:
    template_id: str
    label: str
    defaults: BuilderFormValues


_DIGEST_DEFAULTS = BuilderFormValues(
    "status-digest", "Concise status reporter.", "Summarize queue health.", ("status-summary",), "routine-default"
)
DEFAULT_BUILDER_TEMPLATES: tuple[BuilderTemplate, ...] = (
    BuilderTemplate("status-digest", "Fleet Status Digest", _DIGEST_DEFAULTS),
)


@dataclass(frozen=True)
class FleetBotListItem:
    role_id: str
    name: str
    current_version_id: str
    host: str
    leader: str
    state: str
    activation_gated: bool


@dataclass(frozen=True)
class BuilderPreviewView:
    validation_ok: bool
    policy_findings: tuple[str, ...]
    this_version_diff: tuple[str, ...]
    cumulative_diff: tuple[str, ...]
    confirmation_triggers: tuple[str, ...]
    requires_standalone_approval: bool
    host_effects: dict[str, object]
    export_effects: dict[str, object]


@dataclass(frozen=True)
class StandaloneApprovalScreen:
    role_name: str
    cumulative_diff_vs_original: tuple[str, ...]
    this_version_diff: tuple[str, ...]
    plain_english_summary: str
    bundling_violation: bool
    confirmed: bool = False


@dataclass(frozen=True)
class VersionHistoryEntry:
    version_id: str
    sequence: int
    content_hash: str
    rolled_back_from: str | None
    is_current: bool
    enabled: bool
    can_rollback: bool


@dataclass(frozen=True)
class DryRunReviewPanel:
    run_id: str
    role_id: str
    version_id: str
    content_hash: str
    completed: bool
    recorded_actions: tuple[DryRunRecordedAction, ...]
    all_mutations_refused: bool
    activate_button_enabled: bool


def render_fleet_bot_list(
    ledgers: Mapping[str, RoleVersionLedger], *, leaders: Mapping[str, str] | None = None
) -> tuple[FleetBotListItem, ...]:
    leader_map = leaders or {}
    items: list[FleetBotListItem] = []
    for role_id, ledger in ledgers.items():
        cur = ledger.current
        if cur is None:
            continue
        host, vid, name = cur.role.host, cur.version_id, cur.role.name
        explicit = leader_map.get(role_id, "").strip()
        leader = explicit if explicit else ("rd" if host == "rd-first" else "grokbot")
        state = "on" if ledger.is_active else "off"
        items.append(FleetBotListItem(role_id, name, vid, host, leader, state, ledger.activation_gated))
    return tuple(items)


def build_preview_view(
    draft: Draft,
    ceiling: GrantCeiling,
    *,
    schedule_policy: SchedulePolicy = DEFAULT_SCHEDULE_POLICY,
    declared_edges_by_role: Mapping[str, Sequence[TriggerEdge]] | None = None,
) -> BuilderPreviewView:
    val = validate_draft(draft, ceiling, schedule_policy=schedule_policy, declared_edges_by_role=declared_edges_by_role)
    prv = preview_draft(draft, ceiling)
    diff = prv.diff
    this_diff = list(diff.newly_able_vs_previous) if diff else []
    if diff:
        this_diff.extend(r for r in diff.requires_confirmation if r not in this_diff)
    cum_diff = list(diff.newly_able_vs_original) if diff else []
    triggers = diff.requires_confirmation if diff else ()
    req = bool(this_diff or cum_diff or triggers)
    t_diff, c_diff = tuple(this_diff), tuple(cum_diff)
    return BuilderPreviewView(val.ok, val.reasons, t_diff, c_diff, triggers, req, prv.host_effects, prv.export_effects)


def _has_unrelated_bundled_edits(prev: BuilderRoleDefinition, cand: BuilderRoleDefinition) -> bool:
    return (
        prev.description != cand.description
        or tuple(prev.skills) != tuple(cand.skills)
        or prev.model != cand.model
        or prev.host != cand.host
    )


def build_approval_screen(draft: Draft) -> StandaloneApprovalScreen:
    if draft.baseline_previous is None or draft.baseline_original is None:
        raise BuilderUiError("standalone approval screen requires an existing baseline version")
    diff = escalation_diff(draft.baseline_original, draft.baseline_previous, draft.role)
    this_items = list(diff.newly_able_vs_previous) + [
        r for r in diff.requires_confirmation if r not in diff.newly_able_vs_previous
    ]
    cum_items = list(diff.newly_able_vs_original)
    for f in ("persona", "instructions"):
        if " ".join(getattr(draft.baseline_original, f).split()) != " ".join(getattr(draft.role, f).split()):
            if f not in cum_items:
                cum_items.append(f)
    clauses = (
        [f"use new capabilities ({', '.join(diff.newly_able_vs_previous)})"] if diff.newly_able_vs_previous else []
    )
    for r in diff.requires_confirmation:
        clauses.append(f"update {r}" if r in {"persona", "instructions"} else f"widen {r}")
    summary = "What this bot could newly do: " + ("; ".join(clauses) if clauses else "no new capabilities") + "."
    bundled = bool(this_items or cum_items) and _has_unrelated_bundled_edits(draft.baseline_previous, draft.role)
    return StandaloneApprovalScreen(draft.role.name, tuple(cum_items), tuple(this_items), summary, bundled, False)


def render_version_history(ledger: RoleVersionLedger) -> tuple[VersionHistoryEntry, ...]:
    cid = ledger.current.version_id if ledger.current is not None else None
    items: list[VersionHistoryEntry] = []
    for v in ledger.history:
        vid, seq, ch, rb, en = v.version_id, v.sequence, v.content_hash, v.rolled_back_from, v.role.enabled
        items.append(VersionHistoryEntry(vid, seq, ch, rb, vid == cid, en, vid != cid))
    return tuple(items)


def review_dry_run(ledger: RoleVersionLedger, dry_run: DryRunRecord) -> DryRunReviewPanel:
    cur, acts = ledger.current, dry_run.recorded_actions
    refused = all(a.refused for a in acts)
    rid, ver, ch = dry_run.role_id, dry_run.version_id, dry_run.content_hash
    matches = cur is not None and rid == ledger.role_id and ver == cur.version_id and ch == cur.content_hash
    ok = bool(dry_run.completed and matches and refused)
    return DryRunReviewPanel(dry_run.run_id, rid, ver, ch, dry_run.completed, acts, refused, ok)


class BuilderWorkspaceController:
    """Stateful UI controller enforcing draft -> validate -> preview -> approval -> publish."""

    def __init__(
        self,
        *,
        ceiling: GrantCeiling,
        ledgers: dict[str, RoleVersionLedger],
        schedule_policy: SchedulePolicy = DEFAULT_SCHEDULE_POLICY,
    ) -> None:
        self._ceiling = ceiling
        self._ledgers = ledgers
        self._schedule_policy = schedule_policy
        self._draft: Draft | None = None
        self._approval_screen: StandaloneApprovalScreen | None = None

    @staticmethod
    def reject_hold_or_ceiling_edit(payload: Mapping[str, object]) -> None:
        for key in payload:
            low = key.lower()
            if key in _HOLD_KEYS or low.startswith("hold_") or low.endswith("_hold"):
                raise BuilderUiError("UI cannot create, edit, or lift a hold")
            if key in _CEILING_KEYS or "ceiling" in low:
                raise BuilderUiError("UI cannot edit the locked grant ceiling")

    def _kw(self, edges: Mapping[str, Sequence[TriggerEdge]] | None) -> dict[str, object]:
        return {"schedule_policy": self._schedule_policy, "declared_edges_by_role": edges}

    def start_draft(
        self, mode: DraftMode, form: BuilderFormValues, *, role_id: str | None = None, source: RoleVersion | None = None
    ) -> Draft:
        open_editor_view(BUILDER_EDITOR_V1)
        ledger = self._ledgers.get(role_id or form.name) if mode is DraftMode.EDIT else None
        self._draft = create_draft(mode, form.to_payload(), ledger=ledger, source=source)
        self._approval_screen = None
        return self._draft

    def validate_current(
        self, *, declared_edges_by_role: Mapping[str, Sequence[TriggerEdge]] | None = None
    ) -> ValidationResult:
        if self._draft is None:
            raise BuilderUiError("no active draft to validate")
        return validate_draft(self._draft, self._ceiling, **self._kw(declared_edges_by_role))  # type: ignore[arg-type]

    def preview_current(
        self, *, declared_edges_by_role: Mapping[str, Sequence[TriggerEdge]] | None = None
    ) -> BuilderPreviewView:
        if self._draft is None:
            raise BuilderUiError("no active draft to preview")
        return build_preview_view(self._draft, self._ceiling, **self._kw(declared_edges_by_role))  # type: ignore[arg-type]

    def open_approval_screen(self) -> StandaloneApprovalScreen:
        if self._draft is None:
            raise BuilderUiError("no active draft for approval screen")
        self._approval_screen = build_approval_screen(self._draft)
        return self._approval_screen

    def confirm_approval(self) -> StandaloneApprovalScreen:
        if self._approval_screen is None:
            raise BuilderUiError("open the standalone approval screen before confirming")
        if self._approval_screen.bundling_violation:
            raise BuilderUiError("approval refused: edit is bundled with unrelated metadata edits")
        self._approval_screen = dataclasses.replace(self._approval_screen, confirmed=True)
        return self._approval_screen

    async def publish_current(
        self, *, principal: str, declared_edges_by_role: Mapping[str, Sequence[TriggerEdge]] | None = None
    ) -> RoleVersion:
        if self._draft is None:
            raise BuilderUiError("no active draft to publish")
        preview = self.preview_current(declared_edges_by_role=declared_edges_by_role)
        if preview.requires_standalone_approval and (not self._approval_screen or not self._approval_screen.confirmed):
            raise BuilderUiError("publish blocked: standalone approval screen confirmation required")
        role_id = self._draft.role.name
        ledger = self._ledgers.setdefault(role_id, RoleVersionLedger(role_id=role_id))
        ok = bool(self._approval_screen and self._approval_screen.confirmed)
        kw = self._kw(declared_edges_by_role)
        ver = await publish_draft(self._draft, self._ceiling, ledger, confirmed=ok, principal=principal, **kw)  # type: ignore[arg-type]
        self._draft = None
        self._approval_screen = None
        return ver

    async def rollback_role(self, role_id: str, target_version_id: str, *, principal: str) -> RoleVersion:
        if role_id not in self._ledgers:
            raise BuilderUiError(f"unknown role_id: {role_id!r}")
        return await rollback_publish(self._ledgers[role_id], target_version_id, self._ceiling, principal=principal)

    async def activate_role(
        self, role_id: str, dry_run: DryRunRecord, *, approver: str, notes: str = ""
    ) -> ApprovalRecord:
        if role_id not in self._ledgers:
            raise BuilderUiError(f"unknown role_id: {role_id!r}")
        if not review_dry_run(self._ledgers[role_id], dry_run).activate_button_enabled:
            raise BuilderUiError("activate button disabled until supervised dry run completes cleanly")
        return await approve_activation(self._ledgers[role_id], dry_run, approver=approver, notes=notes)
