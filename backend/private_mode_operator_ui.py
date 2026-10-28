"""Private Mode PM-19 operator UI view-model projections and audit viewer (issue #3270, epic #3249).

Provides classification badges, metadata-only thread/panel projections for ``SENSITIVE``
work, parked reason/deadline views over PM-10/PM-11 records, and an operator audit
viewer gated on the ``staff.audit.read`` scope. Reads API projections only (LoD) and
reuses PM-4/PM-6/PM-9/PM-10/PM-11 primitives (DRY).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from private_context_audit import PrivateContextAuditStore, PrivateModeAuditEvent
from private_mode import (
    DISABLED,
    ClassificationEnvelope,
    ClassificationError,
    Level,
    PrivateModeConfig,
    validate_envelope,
)
from private_mode_admission import PARKED_STATES, AdmissionDecision, validate_sanitized_reason
from private_mode_emit import SharedProjection, opaque_identifier
from private_mode_parking import BoundedParkingQueue, ParkedJobRecord, ParkingState
from private_mode_sinks import SinkEmission

AUDIT_READ_SCOPE = "staff.audit.read"
_TONE_BY_LEVEL: Mapping[Level, str] = {
    Level.PUBLIC: "neutral",
    Level.INTERNAL: "info",
    Level.SENSITIVE: "warning",
}
_WITHHELD_THREAD_PLACEHOLDER = "[SENSITIVE thread: metadata-only projection]"


@dataclass(frozen=True)
class ClassificationBadge:
    """Operator UI classification badge model."""

    level: Level
    label: str
    tone: str
    visible: bool
    withheld: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.name,
            "label": self.label,
            "tone": self.tone,
            "visible": self.visible,
            "withheld": self.withheld,
        }


@dataclass(frozen=True)
class OperatorThreadView:
    """Operator UI thread/panel view derived strictly from a :class:`SharedProjection`."""

    thread_id: str
    badge: ClassificationBadge
    metadata_only: bool
    metadata: Mapping[str, Any]
    body_md: str | None = None
    placeholder: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "thread_id": self.thread_id,
            "badge": self.badge.to_dict(),
            "metadata_only": self.metadata_only,
            "metadata": dict(self.metadata),
        }
        if self.body_md is not None:
            out["body_md"] = self.body_md
        if self.placeholder is not None:
            out["placeholder"] = self.placeholder
        return out


@dataclass(frozen=True)
class OperatorParkedItemView:
    """Operator UI view for a parked or expired PM-10/PM-11 job."""

    job_id: str
    state: str
    reason: str
    owner: str
    deadline: str
    badge: ClassificationBadge
    parked_at: str | None = None
    expired_at: str | None = None
    escalated_to: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "state": self.state,
            "reason": self.reason,
            "owner": self.owner,
            "deadline": self.deadline,
            "badge": self.badge.to_dict(),
            "parked_at": self.parked_at,
            "expired_at": self.expired_at,
            "escalated_to": self.escalated_to,
        }


@dataclass(frozen=True)
class OperatorAuditRow:
    """One metadata-only row in the operator audit viewer."""

    seq: int
    ts: str
    action: str
    actor: str
    target_id: str
    level: Level
    outcome: str
    metadata: Mapping[str, str | int | bool]
    entry_hash: str
    badge: ClassificationBadge

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "action": self.action,
            "actor": self.actor,
            "target_id": self.target_id,
            "level": self.level.name,
            "outcome": self.outcome,
            "metadata": dict(self.metadata),
            "entry_hash": self.entry_hash,
            "badge": self.badge.to_dict(),
        }


@dataclass(frozen=True)
class OperatorAuditView:
    """Verified metadata-only audit trail view for operators bearing ``staff.audit.read``."""

    rows: tuple[OperatorAuditRow, ...]
    chain_ok: bool
    checked_count: int
    chain_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [r.to_dict() for r in self.rows],
            "chain_ok": self.chain_ok,
            "checked_count": self.checked_count,
            "chain_reason": self.chain_reason,
        }


def _require_config(config: object) -> PrivateModeConfig:
    if not isinstance(config, PrivateModeConfig):
        raise ClassificationError("operator_ui: config must be a PrivateModeConfig")
    return config


def build_classification_badge(
    source: Level | ClassificationEnvelope | SharedProjection | SinkEmission,
    *,
    config: PrivateModeConfig = DISABLED,
) -> ClassificationBadge:
    """Build a :class:`ClassificationBadge` from a classification or projection object (DbC)."""
    cfg = _require_config(config)
    if isinstance(source, SinkEmission):
        raw_level = source.projection.level
    elif isinstance(source, SharedProjection):
        raw_level = source.level
    elif isinstance(source, ClassificationEnvelope):
        raw_level = validate_envelope(source).level
    elif isinstance(source, Level):
        raw_level = source
    else:
        raise ClassificationError("build_classification_badge: unsupported source type")

    eff_level = raw_level if cfg.enabled else Level.PUBLIC
    return ClassificationBadge(
        level=eff_level,
        label=eff_level.name,
        tone=_TONE_BY_LEVEL[eff_level],
        visible=cfg.enabled,
        withheld=cfg.enabled and eff_level is Level.SENSITIVE,
    )


def project_thread_for_operator_ui(
    source: SharedProjection | SinkEmission,
    *,
    config: PrivateModeConfig = DISABLED,
) -> OperatorThreadView:
    """Project a thread/panel emission into an :class:`OperatorThreadView` (LoD: reads projection only)."""
    cfg = _require_config(config)
    if isinstance(source, SinkEmission):
        proj = source.projection
    elif isinstance(source, SharedProjection):
        proj = source
    else:
        raise ClassificationError("project_thread_for_operator_ui: source must be a SharedProjection or SinkEmission")

    badge = build_classification_badge(proj, config=cfg)
    if cfg.enabled and (proj.withheld or proj.level is Level.SENSITIVE):
        return OperatorThreadView(
            thread_id=proj.opaque_id,
            badge=badge,
            metadata_only=True,
            metadata=dict(proj.metadata),
            body_md=None,
            placeholder=_WITHHELD_THREAD_PLACEHOLDER,
        )

    content_map = proj.content if isinstance(proj.content, Mapping) else {}
    raw_id = content_map.get("id") or content_map.get("thread_id") or proj.opaque_id
    raw_body = content_map.get("body_md") or content_map.get("body") or content_map.get("summary")
    body_str = str(raw_body) if isinstance(raw_body, str) else None
    return OperatorThreadView(
        thread_id=str(raw_id),
        badge=badge,
        metadata_only=False,
        metadata=dict(proj.metadata),
        body_md=body_str,
        placeholder=None,
    )


def project_parked_item_for_operator_ui(
    item: AdmissionDecision | ParkedJobRecord,
    *,
    config: PrivateModeConfig = DISABLED,
) -> OperatorParkedItemView:
    """Project a parked :class:`AdmissionDecision` or :class:`ParkedJobRecord` for the operator UI."""
    cfg = _require_config(config)
    if isinstance(item, AdmissionDecision):
        if item.state not in PARKED_STATES or not item.deadline:
            raise ClassificationError(
                "project_parked_item_for_operator_ui: AdmissionDecision must be in a parked state"
            )
        level = item.effective_level
        state_str = item.state.value
        reason = validate_sanitized_reason(item.reason)
        owner = item.owner.strip()
        deadline = item.deadline.strip()
        parked_at: str | None = None
        expired_at: str | None = None
        escalated_to: str | None = None
        raw_job_id = item.job_id
    elif isinstance(item, ParkedJobRecord):
        if not isinstance(item.state, ParkingState):
            raise ClassificationError("project_parked_item_for_operator_ui: invalid ParkedJobRecord state")
        level = item.level
        state_str = item.state.value
        reason = validate_sanitized_reason(item.reason)
        owner = item.owner.strip()
        deadline = item.deadline.strip()
        parked_at = item.parked_at
        expired_at = item.expired_at
        escalated_to = item.escalated_to
        raw_job_id = item.job_id
    else:
        raise ClassificationError(
            "project_parked_item_for_operator_ui: item must be an AdmissionDecision or ParkedJobRecord"
        )

    badge = build_classification_badge(level, config=cfg)
    display_job_id = opaque_identifier(raw_job_id) if badge.withheld else raw_job_id
    return OperatorParkedItemView(
        job_id=display_job_id,
        state=state_str,
        reason=reason,
        owner=owner,
        deadline=deadline,
        badge=badge,
        parked_at=parked_at,
        expired_at=expired_at,
        escalated_to=escalated_to,
    )


def list_parked_operator_views(
    queue: BoundedParkingQueue,
    *,
    level: Level | None = None,
    config: PrivateModeConfig = DISABLED,
) -> tuple[OperatorParkedItemView, ...]:
    """Return operator UI views for all active parked and expired records in *queue*."""
    cfg = _require_config(config)
    if not isinstance(queue, BoundedParkingQueue):
        raise ClassificationError("list_parked_operator_views: queue must be a BoundedParkingQueue")
    records = (*queue.list_parked(level=level), *queue.list_expired(level=level))
    return tuple(project_parked_item_for_operator_ui(rec, config=cfg) for rec in records)


def _assert_audit_read_scope(caller_scopes: Iterable[str]) -> None:
    if isinstance(caller_scopes, (str, bytes)) or not isinstance(caller_scopes, Iterable):
        raise ClassificationError("render_audit_viewer: caller_scopes must be an iterable of scope strings")
    scopes = {str(s).strip() for s in caller_scopes if isinstance(s, str)}
    if AUDIT_READ_SCOPE not in scopes:
        raise PermissionError(f"render_audit_viewer: {AUDIT_READ_SCOPE} scope is required")


def _project_audit_row(ev: PrivateModeAuditEvent, *, config: PrivateModeConfig) -> OperatorAuditRow:
    badge = build_classification_badge(ev.level, config=config)
    target_id = opaque_identifier(ev.target_id) if badge.withheld else ev.target_id
    return OperatorAuditRow(
        seq=ev.seq,
        ts=ev.ts,
        action=ev.action,
        actor=ev.actor,
        target_id=target_id,
        level=badge.level,
        outcome=ev.outcome,
        metadata=dict(ev.metadata),
        entry_hash=ev.entry_hash,
        badge=badge,
    )


def render_audit_viewer(
    store: PrivateContextAuditStore,
    *,
    caller_scopes: Iterable[str],
    limit: int = 100,
    config: PrivateModeConfig = DISABLED,
) -> OperatorAuditView:
    """Render metadata-only audit rows and chain verification for callers with ``staff.audit.read``."""
    cfg = _require_config(config)
    if not isinstance(store, PrivateContextAuditStore):
        raise ClassificationError("render_audit_viewer: store must be a PrivateContextAuditStore")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 1000:
        raise ClassificationError("render_audit_viewer: limit must be an int between 1 and 1000")
    _assert_audit_read_scope(caller_scopes)

    chain = store.verify_chain()
    decisions = store.list_decisions(limit=limit)
    rows = tuple(_project_audit_row(ev, config=cfg) for ev in decisions)
    return OperatorAuditView(
        rows=rows,
        chain_ok=chain.ok,
        checked_count=chain.checked_count,
        chain_reason=chain.reason,
    )
