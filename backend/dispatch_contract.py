"""Compatibility shim — re-exports the full dispatch contract public API.

The implementation now lives in ``backend/dispatch/``.  This module exists
so existing imports (``from dispatch_contract import X``, ``import
dispatch_contract``) continue to work unchanged during the v1 → v2 transition
documented in ``backend/dispatch/SCHEMA_MIGRATIONS.md``.

Do not add new logic here — extend the submodules instead.
"""

from dispatch import *  # noqa: F401, F403
from dispatch import (
    CommandEnvelope,
    DispatchConfirmation,
    get_action,
)
from dispatch.envelope import _ensure_dict, _required_string, _utc_now  # noqa: F401
from dispatch.registry import _scheduler_modify_command
from dispatch.signing import (  # noqa: F401
    _load_signing_secret,
    _sign_envelope_payload,
    _validate_timestamp_freshness,
    _verify_envelope_signature,
)


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
) -> CommandEnvelope:
    """Convenience factory — retained for backward compatibility."""
    # Issue #331 — default correlation_id from the active request context so
    # envelopes built during an HTTP request are automatically correlated.
    if not correlation_id:
        try:
            from request_context import current_request_id  # noqa: PLC0415

            correlation_id = current_request_id()
        except ImportError:
            pass
    return CommandEnvelope(
        action=action,
        source=source,
        target=target,
        requested_by=requested_by,
        reason=reason,
        payload=_ensure_dict(payload),
        confirmation=confirmation,
        principal=principal,
        on_behalf_of=on_behalf_of,
        correlation_id=correlation_id,
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
