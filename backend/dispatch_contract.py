"""Hub-to-node dispatch contract primitives.

This module defines a safe prototype foundation for dashboard dispatch:

- command envelopes that are JSON-serializable
- an allowlisted action catalog
- read-only versus privileged action separation
- confirmation gating for privileged actions
- audit-log records for every decision

The module is intentionally side-effect free so it can be exercised in tests
without touching the running dashboard service.
"""

from __future__ import annotations

import datetime as _dt_mod
import hashlib
import hmac
import json
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

SCHEMA_VERSION = "dispatch-envelope.v1"
ENVELOPE_VERSION = 1


def _load_signing_secret() -> str:
    """Load DISPATCH_SIGNING_SECRET from environment or generate/save it."""
    secret = os.environ.get("DISPATCH_SIGNING_SECRET", "").strip()
    if secret:
        return secret

    config_base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    config_dir = os.path.join(config_base, "runner-dashboard")
    key_file = os.path.join(config_dir, "dispatch_signing_key")

    if os.path.exists(key_file):
        with open(key_file) as f:
            return f.read().strip()

    import secrets

    secret = secrets.token_hex(24)
    os.makedirs(config_dir, exist_ok=True)
    with open(key_file, "w") as f:
        f.write(secret)
    os.chmod(key_file, 0o600)
    return secret


class _StrEnum(str, Enum):
    """Python 3.10 compatible StrEnum."""

    pass


class TimestampValidationResult(_StrEnum):
    """Result of timestamp freshness validation."""

    VALID = "valid"
    TOO_OLD = "too_old"
    TOO_NEW = "too_new"
    INVALID_FORMAT = "invalid_format"


def _validate_timestamp_freshness(timestamp_str: str, ttl_seconds: int = 300) -> TimestampValidationResult:
    """Validate that timestamp is within ±ttl_seconds of current time."""
    try:
        if timestamp_str.endswith("Z"):
            ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        else:
            ts = datetime.fromisoformat(timestamp_str)

        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)

        now = datetime.now(UTC)
        delta = abs((now - ts).total_seconds())

        if delta > ttl_seconds:
            return TimestampValidationResult.TOO_OLD if ts < now else TimestampValidationResult.TOO_NEW
        return TimestampValidationResult.VALID
    except (ValueError, AttributeError):
        return TimestampValidationResult.INVALID_FORMAT


def _sign_envelope_payload(
    action: str,
    source: str,
    target: str,
    requested_by: str,
    issued_at: str,
    envelope_version: int,
    secret: str,
) -> str:
    """Generate HMAC-SHA256 signature over envelope payload."""
    canonical = json.dumps(
        {
            "action": action,
            "source": source,
            "target": target,
            "requested_by": requested_by,
            "issued_at": issued_at,
            "envelope_version": envelope_version,
        },
        separators=(",", ":"),
        sort_keys=True,
    )

    return hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def _verify_envelope_signature(
    action: str,
    source: str,
    target: str,
    requested_by: str,
    issued_at: str,
    envelope_version: int,
    signature: str,
    secret: str,
) -> bool:
    """Verify HMAC-SHA256 signature over envelope payload."""
    expected = _sign_envelope_payload(action, source, target, requested_by, issued_at, envelope_version, secret)
    return hmac.compare_digest(expected, signature)


class DispatchAccess(_StrEnum):
    """Access level for an allowlisted dispatch action."""

    READ_ONLY = "read_only"
    PRIVILEGED = "privileged"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _ensure_dict(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return dict(payload)
    raise TypeError("payload must be a mapping")


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data[key]
    if value is None:
        raise ValueError(f"{key} is required")
    text = str(value)
    if not text.strip():
        raise ValueError(f"{key} is required")
    return text


def _confirmation_state(validation: DispatchValidationResult, confirmation: DispatchConfirmation | None) -> str:
    if confirmation is None:
        return "missing" if validation.confirmation_required else "not-required"
    if validation.confirmation_required and validation.reason.startswith("confirmation must "):
        return "invalid"
    return "approved"


@dataclass(frozen=True, slots=True)
class DispatchConfirmation:
    """Human approval metadata required for privileged actions."""

    approved_by: str
    approved_at: str
    envelope_id: str = ""
    approval_hmac: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DispatchConfirmation:
        return cls(
            approved_by=_required_string(data, "approved_by"),
            approved_at=_required_string(data, "approved_at"),
            envelope_id=str(data.get("envelope_id", "")),
            approval_hmac=str(data.get("approval_hmac", "")),
            note="" if data.get("note") is None else str(data.get("note", "")),
        )


@dataclass(frozen=True, slots=True)
class DispatchAction:
    """Allowlisted action definition."""

    name: str
    access: DispatchAccess
    description: str
    prototype_command: tuple[str, ...]
    requires_confirmation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "access": self.access.value,
            "description": self.description,
            "prototype_command": list(self.prototype_command),
            "requires_confirmation": self.requires_confirmation,
        }


@dataclass(frozen=True, slots=True)
class CommandEnvelope:
    """JSON-safe dispatch request sent from hub to node."""

    action: str
    source: str
    target: str
    requested_by: str
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    confirmation: DispatchConfirmation | None = None
    envelope_id: str = field(default_factory=lambda: uuid4().hex)
    schema_version: str = SCHEMA_VERSION
    envelope_version: int = ENVELOPE_VERSION
    issued_at: str = field(default_factory=_utc_now)
    signature: str = ""

    def __post_init__(self) -> None:
        if not self.signature:
            secret = _load_signing_secret()
            sig = _sign_envelope_payload(
                self.action,
                self.source,
                self.target,
                self.requested_by,
                self.issued_at,
                self.envelope_version,
                secret,
            )
            object.__setattr__(self, "signature", sig)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.confirmation is not None:
            data["confirmation"] = self.confirmation.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandEnvelope:
        confirmation_data = data.get("confirmation")
        confirmation = DispatchConfirmation.from_dict(confirmation_data) if confirmation_data is not None else None
        envelope = cls(
            action=_required_string(data, "action"),
            source=_required_string(data, "source"),
            target=_required_string(data, "target"),
            requested_by=_required_string(data, "requested_by"),
            reason=str(data.get("reason", "")),
            payload=_ensure_dict(data.get("payload")),
            confirmation=confirmation,
            envelope_id=str(data.get("envelope_id", uuid4().hex)),
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
            envelope_version=int(data.get("envelope_version", ENVELOPE_VERSION)),
            issued_at=str(data.get("issued_at", _utc_now())),
            signature=str(data.get("signature", "")),
        )
        return envelope

    def verify_signature(self) -> bool:
        """Verify that envelope signature is valid."""
        if not self.signature:
            return False
        try:
            secret = _load_signing_secret()
            return _verify_envelope_signature(
                self.action,
                self.source,
                self.target,
                self.requested_by,
                self.issued_at,
                self.envelope_version,
                self.signature,
                secret,
            )
        except Exception:
            return False


@dataclass(frozen=True, slots=True)
class DispatchAuditLogEntry:
    """Append-only audit record for a dispatch decision."""

    event_id: str
    envelope_id: str
    action: str
    access: DispatchAccess
    source: str
    target: str
    requested_by: str
    decision: str
    detail: str
    confirmation_state: str
    payload_snapshot: dict[str, Any]
    recorded_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "envelope_id": self.envelope_id,
            "action": self.action,
            "access": self.access.value,
            "source": self.source,
            "target": self.target,
            "requested_by": self.requested_by,
            "decision": self.decision,
            "detail": self.detail,
            "confirmation_state": self.confirmation_state,
            "payload_snapshot": dict(self.payload_snapshot),
            "recorded_at": self.recorded_at,
        }


@dataclass(frozen=True, slots=True)
class DispatchValidationResult:
    """Validation outcome for a command envelope."""

    accepted: bool
    reason: str
    action: DispatchAction | None
    confirmation_required: bool


@dataclass(frozen=True, slots=True)
class CryptoValidationResult:
    """Cryptographic validation outcome for an envelope signature and timestamps."""

    valid: bool
    reason: str


def validate_envelope_crypto(envelope: CommandEnvelope) -> CryptoValidationResult:
    """Validate envelope signature and timestamp freshness.

    Returns CryptoValidationResult with valid=True only if all checks pass:
    - Signature is valid (matches HMAC of canonical JSON)
    - Timestamp is fresh (issued_at within ±5 minutes)
    - Confirmation timestamp is fresh if confirmation is present (approved_at within ±5 minutes)
    """
    if not envelope.signature:
        return CryptoValidationResult(valid=False, reason="envelope signature missing")

    if not envelope.verify_signature():
        return CryptoValidationResult(valid=False, reason="envelope signature invalid")

    issued_at_result = _validate_timestamp_freshness(envelope.issued_at, ttl_seconds=300)
    if issued_at_result != TimestampValidationResult.VALID:
        reason_map = {
            TimestampValidationResult.TOO_OLD: "envelope issued_at timestamp too old",
            TimestampValidationResult.TOO_NEW: "envelope issued_at timestamp in future",
            TimestampValidationResult.INVALID_FORMAT: "envelope issued_at timestamp invalid format",
        }
        return CryptoValidationResult(valid=False, reason=reason_map.get(issued_at_result, "unknown timestamp error"))

    if envelope.confirmation is not None:
        approved_at_result = _validate_timestamp_freshness(envelope.confirmation.approved_at, ttl_seconds=300)
        if approved_at_result != TimestampValidationResult.VALID:
            reason_map = {
                TimestampValidationResult.TOO_OLD: "confirmation approved_at timestamp too old",
                TimestampValidationResult.TOO_NEW: "confirmation approved_at timestamp in future",
                TimestampValidationResult.INVALID_FORMAT: "confirmation approved_at timestamp invalid format",
            }
            reason = reason_map.get(approved_at_result, "unknown timestamp error")
            return CryptoValidationResult(valid=False, reason=reason)

    final_reason = "signature and timestamps valid"
    return CryptoValidationResult(valid=True, reason=final_reason)


ALLOWLISTED_ACTIONS: dict[str, DispatchAction] = {
    # ── Read-only actions ────────────────────────────────────────────────────
    "dashboard.status": DispatchAction(
        name="dashboard.status",
        access=DispatchAccess.READ_ONLY,
        description="Read-only dashboard health and status snapshot.",
        prototype_command=("python3", "-m", "json.tool"),
    ),
    "runner.status": DispatchAction(
        name="runner.status",
        access=DispatchAccess.READ_ONLY,
        description="List runner services and their systemd status on the node.",
        prototype_command=("systemctl", "status", "actions.runner.*"),
    ),
    "scheduler.list": DispatchAction(
        name="scheduler.list",
        access=DispatchAccess.READ_ONLY,
        description="List scheduled maintenance jobs known to the node.",
        prototype_command=("systemctl", "list-timers", "--all"),
    ),
    # ── Privileged actions (require explicit confirmation) ────────────────────
    "dashboard.update_and_restart": DispatchAction(
        name="dashboard.update_and_restart",
        access=DispatchAccess.PRIVILEGED,
        description="Apply the deployed dashboard update helper and restart.",
        prototype_command=("bash", "runner-dashboard/deploy/update-deployed.sh"),
        requires_confirmation=True,
    ),
    "runner.restart": DispatchAction(
        name="runner.restart",
        access=DispatchAccess.PRIVILEGED,
        description="Restart one or all GitHub Actions runner services on the node.",
        prototype_command=("sudo", "systemctl", "restart", "actions.runner.*"),
        requires_confirmation=True,
    ),
    "runner.stop": DispatchAction(
        name="runner.stop",
        access=DispatchAccess.PRIVILEGED,
        description=("Stop one or all GitHub Actions runner services. Destructive: in-flight jobs will be abandoned."),
        prototype_command=("sudo", "systemctl", "stop", "actions.runner.*"),
        requires_confirmation=True,
    ),
    "service.unregister": DispatchAction(
        name="service.unregister",
        access=DispatchAccess.PRIVILEGED,
        description=(
            "Remove a runner or service registration from the node. "
            "Destructive: cannot be undone without re-registration."
        ),
        prototype_command=("sudo", "systemctl", "disable", "--now"),
        requires_confirmation=True,
    ),
    "scheduler.modify": DispatchAction(
        name="scheduler.modify",
        access=DispatchAccess.PRIVILEGED,
        description=("Enable or disable a scheduled maintenance job. Affects recurring fleet maintenance windows."),
        prototype_command=("sudo", "systemctl", "enable|disable", "<unit>"),
        requires_confirmation=True,
    ),
    # ── Agent dispatch actions ────────────────────────────────────────────────
    "agents.dispatch.adhoc": DispatchAction(
        name="agents.dispatch.adhoc",
        access=DispatchAccess.PRIVILEGED,
        description="Dispatch an agent for an ad-hoc task via the quick-dispatch workflow.",
        prototype_command=("gh", "workflow", "run", "Agent-Quick-Dispatch.yml"),
        requires_confirmation=True,
    ),
    "agents.dispatch.pr": DispatchAction(
        name="agents.dispatch.pr",
        access=DispatchAccess.PRIVILEGED,
        description="Dispatch agents to one or more pull requests via the Agent-PR-Action workflow.",
        prototype_command=("gh", "workflow", "run", "Agent-PR-Action.yml"),
        requires_confirmation=True,
    ),
    "agents.dispatch.issue": DispatchAction(
        name="agents.dispatch.issue",
        access=DispatchAccess.PRIVILEGED,
        description="Dispatch agents to one or more issues via the Agent-Issue-Action workflow.",
        prototype_command=("gh", "workflow", "run", "Agent-Issue-Action.yml"),
        requires_confirmation=True,
    ),
}


def _scheduler_modify_command(payload: dict[str, Any]) -> tuple[str, ...]:
    raw_mode = payload.get("mode")
    if raw_mode is None and "enabled" in payload:
        raw_mode = "enable" if payload["enabled"] else "disable"
    mode = str(raw_mode or "").strip().lower()
    if mode in {"enable", "enabled", "on", "true"}:
        systemctl_mode = "enable"
    elif mode in {"disable", "disabled", "off", "false"}:
        systemctl_mode = "disable"
    else:
        raise ValueError("scheduler.modify payload must request enable or disable")

    unit = str(
        payload.get("unit") or payload.get("timer") or payload.get("service") or "runner-scheduler.timer"
    ).strip()
    if not unit:
        raise ValueError("scheduler.modify payload must include a systemd unit")
    return ("sudo", "systemctl", systemctl_mode, unit)


def get_action(action_name: str) -> DispatchAction | None:
    return ALLOWLISTED_ACTIONS.get(action_name)


def requires_confirmation(action_name: str) -> bool:
    action = get_action(action_name)
    return bool(action and action.requires_confirmation)


def build_envelope(
    *,
    action: str,
    source: str,
    target: str,
    requested_by: str,
    reason: str = "",
    payload: dict[str, Any] | None = None,
    confirmation: DispatchConfirmation | None = None,
) -> CommandEnvelope:
    return CommandEnvelope(
        action=action,
        source=source,
        target=target,
        requested_by=requested_by,
        reason=reason,
        payload=_ensure_dict(payload),
        confirmation=confirmation,
    )


def validate_envelope(envelope: CommandEnvelope) -> DispatchValidationResult:
    if envelope.schema_version != SCHEMA_VERSION:
        return DispatchValidationResult(
            accepted=False,
            reason=f"unsupported schema version: {envelope.schema_version}",
            action=None,
            confirmation_required=False,
        )

    if not envelope.action:
        return DispatchValidationResult(
            accepted=False,
            reason="action is required",
            action=None,
            confirmation_required=False,
        )

    action = get_action(envelope.action)
    if action is None:
        return DispatchValidationResult(
            accepted=False,
            reason=f"action is not allowlisted: {envelope.action}",
            action=None,
            confirmation_required=False,
        )

    confirmation = envelope.confirmation

    if not envelope.source.strip():
        return DispatchValidationResult(
            accepted=False,
            reason="source is required",
            action=action,
            confirmation_required=action.requires_confirmation,
        )

    if not envelope.target.strip():
        return DispatchValidationResult(
            accepted=False,
            reason="target is required",
            action=action,
            confirmation_required=action.requires_confirmation,
        )

    if not envelope.requested_by.strip():
        return DispatchValidationResult(
            accepted=False,
            reason="requested_by is required",
            action=action,
            confirmation_required=action.requires_confirmation,
        )

    if action.access is DispatchAccess.PRIVILEGED and confirmation is None:
        return DispatchValidationResult(
            accepted=False,
            reason=f"confirmation required for privileged action: {action.name}",
            action=action,
            confirmation_required=True,
        )

    # The guard above returns early only for PRIVILEGED+None; READ_ONLY actions
    # may reach this point with confirmation=None, so gate checks on access level.
    if action.access is DispatchAccess.PRIVILEGED and (confirmation is None or not confirmation.approved_by.strip()):
        return DispatchValidationResult(
            accepted=False,
            reason="confirmation must record approved_by",
            action=action,
            confirmation_required=True,
        )

    if action.access is DispatchAccess.PRIVILEGED and (confirmation is None or not confirmation.approved_at.strip()):
        return DispatchValidationResult(
            accepted=False,
            reason="confirmation must record approved_at",
            action=action,
            confirmation_required=True,
        )

    if action.name == "scheduler.modify":
        try:
            _scheduler_modify_command(envelope.payload)
        except ValueError as exc:
            return DispatchValidationResult(
                accepted=False,
                reason=str(exc),
                action=action,
                confirmation_required=action.requires_confirmation,
            )

    return DispatchValidationResult(
        accepted=True,
        reason="accepted",
        action=action,
        confirmation_required=action.requires_confirmation,
    )


def build_audit_log_entry(
    envelope: CommandEnvelope,
    validation: DispatchValidationResult,
    *,
    detail: str = "",
) -> DispatchAuditLogEntry:
    action = validation.action
    access = DispatchAccess.READ_ONLY if action is None else action.access

    return DispatchAuditLogEntry(
        event_id=uuid4().hex,
        envelope_id=envelope.envelope_id,
        action=envelope.action,
        access=access,
        source=envelope.source,
        target=envelope.target,
        requested_by=envelope.requested_by,
        decision="accepted" if validation.accepted else "rejected",
        detail=detail or validation.reason,
        confirmation_state=_confirmation_state(validation, envelope.confirmation),
        payload_snapshot=dict(envelope.payload),
        recorded_at=_utc_now(),
    )


def command_preview(action_name: str, payload: dict[str, Any] | None = None) -> tuple[str, ...]:
    action = get_action(action_name)
    if action is None:
        raise KeyError(action_name)
    if action.name == "scheduler.modify":
        return _scheduler_modify_command(_ensure_dict(payload))
    return action.prototype_command
