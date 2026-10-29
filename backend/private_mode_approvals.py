"""Separation of duties for declassify and cloud approval (issue #3263, PM-12).

Enforces that declassifying an envelope or dispatching ``SENSITIVE`` work to a
cloud provider requires an immutable :class:`SeparationApproval` issued by a
principal distinct from the requester, bound to ``target_id`` (scope),
``destination``, ``expires_at`` and ``policy_version``, and audited in
:mod:`private_context_audit` on every issuance and verification.
"""

from __future__ import annotations

import datetime as _dt_mod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from private_mode import (
    DISABLED,
    ENVELOPE_SCHEMA_VERSION,
    ClassificationEnvelope,
    ClassificationError,
    Level,
    PrivateModeConfig,
    _record_level,
    stamp_envelope,
    validate_envelope,
)

_UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
APPROVAL_KINDS: frozenset[str] = frozenset({"declassify", "cloud_dispatch"})
EXPORTS: frozenset[str] = frozenset(
    {
        "APPROVAL_KINDS",
        "SeparationApproval",
        "authorize_cloud_dispatch",
        "declassify_envelope",
        "issue_approval",
        "verify_approval",
    }
)


def _parse_iso_utc(raw: str, where: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ClassificationError(f"{where}: timestamp must be a non-empty str")
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ClassificationError(f"{where}: invalid ISO-8601 timestamp") from exc
    if dt.tzinfo is None:
        raise ClassificationError(f"{where}: timestamp must include UTC offset")
    return dt.astimezone(_UTC)


@dataclass(frozen=True)
class SeparationApproval:
    """Immutable separation-of-duties approval record (issue #3263, PM-12)."""

    kind: str
    requester: str
    approver: str
    target_id: str
    destination: str
    expires_at: str
    policy_version: str
    target_level: Level = Level.PUBLIC

    def __post_init__(self) -> None:
        fields = ("kind", "requester", "approver", "target_id", "destination", "expires_at", "policy_version")
        for name in fields:
            val = getattr(self, name)
            if not isinstance(val, str) or not val.strip():
                raise ClassificationError(f"SeparationApproval: {name} must be a non-empty str")
        if self.kind not in APPROVAL_KINDS:
            raise ClassificationError("SeparationApproval: unsupported approval kind")
        if not isinstance(self.target_level, Level):
            raise ClassificationError("SeparationApproval: target_level must be a Level")
        _parse_iso_utc(self.expires_at, "SeparationApproval.expires_at")
        if self.requester.strip().lower() == self.approver.strip().lower():
            raise ClassificationError("SeparationApproval: self-approval is refused (approver must differ)")


def _record_approval_audit(
    action: str,
    actor: str,
    target_id: str,
    level: Level,
    outcome: str,
    metadata: Mapping[str, str | int | bool],
    config: PrivateModeConfig,
    audit_store: Any | None = None,
) -> None:
    if not config.enabled and audit_store is None:
        return
    if not hasattr(_dt_mod, "UTC"):
        _dt_mod.UTC = _dt_mod.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017
    from private_context_audit import PrivateModeAuditEvent, get_audit_store  # noqa: PLC0415

    store = audit_store if audit_store is not None else get_audit_store()
    event = PrivateModeAuditEvent(
        action=action,
        actor=actor.strip() if isinstance(actor, str) and actor.strip() else "unknown",
        target_id=target_id.strip() if isinstance(target_id, str) and target_id.strip() else "unknown",
        level=level,
        outcome=outcome,
        metadata=metadata,
    )
    res = store.record_decision(event, config=config)
    if config.enabled and (res.parked or res.state != "OK"):
        raise ClassificationError("separation_of_duties: audit write failed (fail-closed)")


def issue_approval(
    *,
    kind: str,
    requester: str,
    approver: str,
    target_id: str,
    destination: str,
    expires_at: str,
    policy_version: str,
    target_level: Level = Level.PUBLIC,
    level: Level = Level.SENSITIVE,
    config: PrivateModeConfig = DISABLED,
    audit_store: Any | None = None,
) -> SeparationApproval:
    """Issue and audit an immutable separation-of-duties approval record."""
    if not isinstance(config, PrivateModeConfig):
        raise ClassificationError("issue_approval: config must be a PrivateModeConfig")
    meta = {
        "kind": str(kind),
        "requester": str(requester).strip(),
        "destination": str(destination).strip(),
        "policy_version": str(policy_version).strip(),
    }
    if (
        isinstance(requester, str)
        and isinstance(approver, str)
        and requester.strip().lower() == approver.strip().lower()
    ):
        _record_approval_audit(
            f"approval.{kind}", approver, target_id, level, "refused-self-approval", meta, config, audit_store
        )
        raise ClassificationError("issue_approval: self-approval is refused")
    approval = SeparationApproval(
        kind=kind,
        requester=requester.strip() if isinstance(requester, str) else requester,
        approver=approver.strip() if isinstance(approver, str) else approver,
        target_id=target_id,
        destination=destination,
        expires_at=expires_at,
        policy_version=policy_version,
        target_level=target_level,
    )
    _record_approval_audit(
        f"approval.{approval.kind}",
        approval.approver,
        approval.target_id,
        level,
        "approval-issued",
        meta,
        config,
        audit_store,
    )
    return approval


def verify_approval(
    approval: SeparationApproval | None,
    *,
    expected_kind: str,
    requester: str,
    target_id: str,
    destination: str,
    policy_version: str,
    reference_time: str | None = None,
    level: Level = Level.SENSITIVE,
    config: PrivateModeConfig = DISABLED,
    audit_store: Any | None = None,
) -> bool:
    """Verify an approval against scope, destination, expiry and policy_version; audit every check."""
    if not isinstance(config, PrivateModeConfig):
        raise ClassificationError("verify_approval: config must be a PrivateModeConfig")
    if not config.enabled:
        return True
    meta = {"kind": expected_kind, "destination": str(destination), "policy_version": str(policy_version)}

    def _refuse(outcome: str, msg: str, actor: str = requester) -> None:
        _record_approval_audit(
            f"approval.verify.{expected_kind}", actor, target_id, level, outcome, meta, config, audit_store
        )
        raise ClassificationError(f"verify_approval: {msg}")

    if not isinstance(approval, SeparationApproval):
        _refuse("refused-missing-approval", "approval is required")
    assert isinstance(approval, SeparationApproval)
    checks = (
        (
            approval.requester.strip().lower() == approval.approver.strip().lower(),
            "refused-self-approval",
            "self-approval is refused",
        ),
        (approval.kind != expected_kind, "refused-kind-mismatch", "approval kind mismatch"),
        (approval.requester.strip() != requester.strip(), "refused-requester-mismatch", "approval requester mismatch"),
        (approval.target_id != target_id, "refused-scope-mismatch", "approval scope (target_id) mismatch"),
        (approval.destination != destination, "refused-destination-mismatch", "approval destination mismatch"),
        (
            approval.policy_version != policy_version,
            "refused-policy-version-mismatch",
            "approval policy_version mismatch",
        ),
    )
    for failed, outcome, msg in checks:
        if failed:
            _refuse(outcome, msg, actor=approval.approver)
    now_dt = _parse_iso_utc(reference_time, "verify_approval.reference_time") if reference_time else datetime.now(_UTC)
    if now_dt >= _parse_iso_utc(approval.expires_at, "verify_approval.expires_at"):
        _refuse("refused-expired", "approval has expired", actor=approval.approver)
    _record_approval_audit(
        f"approval.verify.{expected_kind}", approval.approver, target_id, level, "approved", meta, config, audit_store
    )
    return True


def declassify_envelope(
    envelope: ClassificationEnvelope | Mapping[str, Any],
    *,
    target_level: Level,
    requester: str,
    target_id: str,
    policy_version: str,
    approval: SeparationApproval | None = None,
    reference_time: str | None = None,
    config: PrivateModeConfig = DISABLED,
    audit_store: Any | None = None,
) -> ClassificationEnvelope:
    """Downgrade an envelope only with a verified, second-principal declassify approval."""
    validated = validate_envelope(envelope)
    if not isinstance(target_level, Level):
        raise ClassificationError("declassify_envelope: target_level must be a Level")
    if not isinstance(config, PrivateModeConfig):
        raise ClassificationError("declassify_envelope: config must be a PrivateModeConfig")
    if not config.enabled:
        return stamp_envelope(Level.PUBLIC, hop="flag_off", config=config)
    verify_approval(
        approval,
        expected_kind="declassify",
        requester=requester,
        target_id=target_id,
        destination=target_level.name,
        policy_version=policy_version,
        reference_time=reference_time,
        level=validated.level,
        config=config,
        audit_store=audit_store,
    )
    assert isinstance(approval, SeparationApproval)
    if approval.target_level != target_level:
        raise ClassificationError("declassify_envelope: target_level destination mismatch")
    _record_level(target_level)
    return ClassificationEnvelope(
        schema_version=ENVELOPE_SCHEMA_VERSION,
        level=target_level,
        epoch=validated.epoch + 1,
        hops=(*validated.hops, "declassify"),
    )


def authorize_cloud_dispatch(
    *,
    requester: str,
    target_id: str,
    destination: str,
    policy_version: str,
    level: Level = Level.SENSITIVE,
    approval: SeparationApproval | None = None,
    reference_time: str | None = None,
    config: PrivateModeConfig = DISABLED,
    audit_store: Any | None = None,
) -> bool:
    """Verify second-principal cloud approval for SENSITIVE work before any cloud dispatch."""
    if not isinstance(level, Level):
        raise ClassificationError("authorize_cloud_dispatch: level must be a Level")
    if not isinstance(config, PrivateModeConfig):
        raise ClassificationError("authorize_cloud_dispatch: config must be a PrivateModeConfig")
    if not config.enabled or level < Level.SENSITIVE:
        return True
    return verify_approval(
        approval,
        expected_kind="cloud_dispatch",
        requester=requester,
        target_id=target_id,
        destination=destination,
        policy_version=policy_version,
        reference_time=reference_time,
        level=level,
        config=config,
        audit_store=audit_store,
    )
