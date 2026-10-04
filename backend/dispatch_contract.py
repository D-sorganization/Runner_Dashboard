"""Compatibility shim — re-exports the full dispatch contract public API.

The implementation now lives in ``backend/dispatch/``.  This module exists
so existing imports (``from dispatch_contract import X``, ``import
dispatch_contract``) continue to work unchanged during the v1 → v2 transition
documented in ``backend/dispatch/SCHEMA_MIGRATIONS.md``.

Do not add new logic here — extend the submodules instead.
"""

from dispatch import (  # noqa: F401
    ALLOWLISTED_ACTIONS,
    ENVELOPE_VERSION,
    MAX_ENVELOPE_VERSION,
    MIN_ENVELOPE_VERSION,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    CommandEnvelope,
    CryptoValidationResult,
    DispatchAccess,
    DispatchAction,
    DispatchAuditLogEntry,
    DispatchConfirmation,
    DispatchValidationResult,
    TimestampValidationResult,
    _compute_approval_hmac,
    _hash_payload,
    build_audit_log_entry,
    get_action,
    requires_confirmation,
    sign_payload,
    validate_envelope,
    validate_envelope_crypto,
    validate_timestamp_freshness,
    verify_approval_hmac,
    verify_payload,
)
from dispatch.envelope import _ensure_dict, _required_string, _utc_now  # noqa: F401
from dispatch.registry import _scheduler_modify_command
from dispatch.signing import (  # noqa: F401
    _load_signing_secret,
    _sign_envelope_payload,
    _validate_timestamp_freshness,
    _verify_envelope_signature,
)
from dispatch_effort import DEFAULT_EFFORT, validate_effort


def build_envelope(
    *,
    action: str,
    source: str,
    target: str,
    requested_by: str,
    reason: str = "",
    payload: dict | None = None,
    confirmation: DispatchConfirmation | None = None,
    principal: str = "",
    on_behalf_of: str = "",
    correlation_id: str = "",
    pr_lifecycle: str | None = None,
    effort: str | None = None,
) -> CommandEnvelope:
    """Convenience factory — retained for backward compatibility.

    ``effort`` (issue #1865) is signed inside ``payload``, which is
    authoritative. It may be given as the argument, as ``payload["effort"]``,
    or both when they agree; with neither, ``DEFAULT_EFFORT`` applies.

    ``pr_lifecycle`` (issue #1874) follows the same rule, defaulting to
    ``"arm_and_exit"``.

    Postcondition: ``envelope.effort == envelope.payload["effort"]`` and
    ``envelope.pr_lifecycle == envelope.payload["pr_lifecycle"]``, so
    ``CommandEnvelope.from_dict(envelope.to_dict())`` accepts the envelope.
    Raises: ValueError when an argument and its payload value disagree.
    """
    # Issue #331 — default correlation_id from the active request context so
    # envelopes built during an HTTP request are automatically correlated.
    if not correlation_id:
        try:
            from request_context import current_request_id  # noqa: PLC0415

            correlation_id = current_request_id()
        except ImportError:
            pass
    payload_dict = _ensure_dict(payload)
    payload_lifecycle = payload_dict.get("pr_lifecycle")
    if pr_lifecycle is not None and payload_lifecycle is not None and pr_lifecycle != payload_lifecycle:
        raise ValueError(f"pr_lifecycle {pr_lifecycle!r} does not match payload pr_lifecycle {payload_lifecycle!r}")
    pr_lifecycle = str(payload_lifecycle if payload_lifecycle is not None else pr_lifecycle or "arm_and_exit")
    payload_dict["pr_lifecycle"] = pr_lifecycle
    # Issue #1865 (USE-1): effort rides in the signed payload as well.
    payload_effort = payload_dict.get("effort")
    if effort is not None and payload_effort is not None and effort != payload_effort:
        raise ValueError(f"effort {effort!r} does not match payload effort {payload_effort!r}")
    effort = validate_effort(payload_effort if payload_effort is not None else effort or DEFAULT_EFFORT)
    payload_dict["effort"] = effort
    return CommandEnvelope(
        action=action,
        source=source,
        target=target,
        requested_by=requested_by,
        reason=reason,
        payload=payload_dict,
        confirmation=confirmation,
        principal=principal,
        on_behalf_of=on_behalf_of,
        correlation_id=correlation_id,
        pr_lifecycle=pr_lifecycle,
        effort=effort,
    )


def command_preview(action_name: str, payload: dict | None = None) -> tuple[str, ...]:
    """Return the prototype command for an action — retained for backward compatibility."""
    action = get_action(action_name)
    if action is None:
        raise KeyError(action_name)
    if action.name == "scheduler.modify":
        return _scheduler_modify_command(_ensure_dict(payload))
    return action.prototype_command


def migrate_envelope_v1_to_v2(envelope: CommandEnvelope) -> CommandEnvelope:
    """Example migration shim for future use. V2 does not exist yet."""
    return envelope
