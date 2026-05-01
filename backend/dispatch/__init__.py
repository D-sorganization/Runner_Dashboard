"""dispatch — modular dispatch contract package.

This package exposes the same public API as the legacy ``dispatch_contract``
module.  ``backend/dispatch_contract.py`` is now a 1-line compatibility shim
that imports from here.

Submodules:
- dispatch.signing   — HMAC signing, verification, timestamp validation
- dispatch.registry  — allowlisted actions, DispatchAccess, DispatchAction
- dispatch.envelope  — CommandEnvelope, DispatchConfirmation, schema constants
- dispatch.validate  — validate_envelope, validate_envelope_crypto, result types
- dispatch.audit     — DispatchAuditLogEntry, build_audit_log_entry
"""

from dispatch.audit import DispatchAuditLogEntry, build_audit_log_entry
from dispatch.envelope import (
    ENVELOPE_VERSION,
    MAX_ENVELOPE_VERSION,
    MIN_ENVELOPE_VERSION,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    CommandEnvelope,
    DispatchConfirmation,
)
from dispatch.registry import (
    ALLOWLISTED_ACTIONS,
    DispatchAccess,
    DispatchAction,
    get_action,
    requires_confirmation,
)
from dispatch.signing import (
    TimestampValidationResult,
    sign_payload,
    validate_timestamp_freshness,
    verify_payload,
)
from dispatch.validate import (
    CryptoValidationResult,
    DispatchValidationResult,
    validate_envelope,
    validate_envelope_crypto,
)

__all__ = [
    # envelope
    "CommandEnvelope",
    "DispatchConfirmation",
    "SCHEMA_VERSION",
    "ENVELOPE_VERSION",
    "MIN_ENVELOPE_VERSION",
    "MAX_ENVELOPE_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    # registry
    "DispatchAccess",
    "DispatchAction",
    "ALLOWLISTED_ACTIONS",
    "get_action",
    "requires_confirmation",
    # signing
    "TimestampValidationResult",
    "sign_payload",
    "verify_payload",
    "validate_timestamp_freshness",
    # validate
    "DispatchValidationResult",
    "CryptoValidationResult",
    "validate_envelope",
    "validate_envelope_crypto",
    # audit
    "DispatchAuditLogEntry",
    "build_audit_log_entry",
]
