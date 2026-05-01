"""Dispatch envelope validation — schema, access, and confirmation checks.

Public API:
- DispatchValidationResult (dataclass)
- CryptoValidationResult (dataclass)
- validate_envelope(envelope) -> DispatchValidationResult
- validate_envelope_crypto(envelope) -> CryptoValidationResult
"""

from __future__ import annotations

from dataclasses import dataclass

from dispatch.registry import DispatchAccess, DispatchAction, _scheduler_modify_command, get_action
from dispatch.signing import TimestampValidationResult, validate_timestamp_freshness


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


def validate_envelope_crypto(envelope: object) -> CryptoValidationResult:
    """Validate envelope signature and timestamp freshness.

    Returns CryptoValidationResult with valid=True only if all checks pass:
    - Signature is valid (matches HMAC of canonical JSON)
    - Timestamp is fresh (issued_at within ±5 minutes)
    - Confirmation timestamp is fresh if confirmation is present (approved_at within ±5 minutes)
    """
    if not envelope.signature:  # type: ignore[attr-defined]
        return CryptoValidationResult(valid=False, reason="envelope signature missing")

    if not envelope.verify_signature():  # type: ignore[attr-defined]
        return CryptoValidationResult(valid=False, reason="envelope signature invalid")

    issued_at_result = validate_timestamp_freshness(envelope.issued_at, ttl_seconds=300)  # type: ignore[attr-defined]
    if issued_at_result != TimestampValidationResult.VALID:
        reason_map = {
            TimestampValidationResult.TOO_OLD: "envelope issued_at timestamp too old",
            TimestampValidationResult.TOO_NEW: "envelope issued_at timestamp in future",
            TimestampValidationResult.INVALID_FORMAT: "envelope issued_at timestamp invalid format",
        }
        return CryptoValidationResult(
            valid=False,
            reason=reason_map.get(issued_at_result, "unknown timestamp error"),
        )

    if envelope.confirmation is not None:  # type: ignore[attr-defined]
        approved_at_result = validate_timestamp_freshness(envelope.confirmation.approved_at, ttl_seconds=300)
        if approved_at_result != TimestampValidationResult.VALID:
            reason_map = {
                TimestampValidationResult.TOO_OLD: "confirmation approved_at timestamp too old",
                TimestampValidationResult.TOO_NEW: "confirmation approved_at timestamp in future",
                TimestampValidationResult.INVALID_FORMAT: "confirmation approved_at timestamp invalid format",
            }
            reason = reason_map.get(approved_at_result, "unknown timestamp error")
            return CryptoValidationResult(valid=False, reason=reason)

    return CryptoValidationResult(valid=True, reason="signature and timestamps valid")


def validate_envelope(envelope: object) -> DispatchValidationResult:
    """Validate action, schema version, required fields, and confirmation."""
    from dispatch.envelope import SUPPORTED_SCHEMA_VERSIONS  # noqa: PLC0415 (avoid circular at module level)

    if envelope.schema_version not in SUPPORTED_SCHEMA_VERSIONS:  # type: ignore[attr-defined]
        return DispatchValidationResult(
            accepted=False,
            reason=f"unsupported schema version: {envelope.schema_version}",  # type: ignore[attr-defined]
            action=None,
            confirmation_required=False,
        )

    if not envelope.action:  # type: ignore[attr-defined]
        return DispatchValidationResult(
            accepted=False,
            reason="action is required",
            action=None,
            confirmation_required=False,
        )

    action = get_action(envelope.action)  # type: ignore[attr-defined]
    if action is None:
        return DispatchValidationResult(
            accepted=False,
            reason=f"action is not allowlisted: {envelope.action}",  # type: ignore[attr-defined]
            action=None,
            confirmation_required=False,
        )

    confirmation = envelope.confirmation  # type: ignore[attr-defined]

    if not envelope.source.strip():  # type: ignore[attr-defined]
        return DispatchValidationResult(
            accepted=False,
            reason="source is required",
            action=action,
            confirmation_required=action.requires_confirmation,
        )

    if not envelope.target.strip():  # type: ignore[attr-defined]
        return DispatchValidationResult(
            accepted=False,
            reason="target is required",
            action=action,
            confirmation_required=action.requires_confirmation,
        )

    if not envelope.requested_by.strip():  # type: ignore[attr-defined]
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
            _scheduler_modify_command(envelope.payload)  # type: ignore[attr-defined]
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
