"""Dispatch signing — HMAC-SHA256 payload signing and verification.

This module is reusable by both the dashboard and Maxwell-Daemon (via shared_scripts/)
for verifying dashboard-issued envelopes per the DRY rule in CLAUDE.md.

Public API:
- sign_payload(canonical_json, secret) -> str
- verify_payload(canonical_json, signature, secret) -> bool
- _load_signing_secret() -> str
- _hash_payload(payload) -> str
- _sign_envelope_payload(...) -> str
- _verify_envelope_signature(...) -> bool
- _compute_approval_hmac(envelope_id, action, secret) -> str
- verify_approval_hmac(confirmation, envelope_id, action) -> bool
- validate_timestamp_freshness(timestamp_str, ttl_seconds) -> TimestampValidationResult
- TimestampValidationResult (enum)
"""

from __future__ import annotations

import datetime as _dt_mod
import enum
import hashlib
import hmac
import json
import logging
import os

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

log = logging.getLogger("dashboard.dispatch.signing")

# Legacy escape hatch (issue #925): when a signing secret IS configured, a
# confirmation that carries no approval HMAC is rejected by default (fail-closed).
# Operators who genuinely still run pre-#318 clients can opt back into the old
# permissive behaviour by setting this flag truthy; doing so logs a deprecation
# warning on every unsigned confirmation so the gap is visible.
_ALLOW_UNSIGNED_APPROVAL_ENV = "DISPATCH_ALLOW_UNSIGNED_APPROVAL"
_TRUTHY = {"1", "true", "yes", "on"}


def _resolve_approval_secret() -> str:
    """Return the configured approval HMAC secret, or '' when none is set.

    Prefers ``APPROVAL_HMAC_SECRET`` and falls back to ``DISPATCH_SIGNING_SECRET``
    so single-secret deployments work without extra configuration.
    """
    secret = os.environ.get("APPROVAL_HMAC_SECRET", "").strip()
    if not secret:
        secret = os.environ.get("DISPATCH_SIGNING_SECRET", "").strip()
    return secret


class _StrEnum(str, enum.Enum):  # noqa: UP042
    """Python 3.10 compatible StrEnum."""

    pass


class TimestampValidationResult(_StrEnum):
    """Result of timestamp freshness validation."""

    VALID = "valid"
    TOO_OLD = "too_old"
    TOO_NEW = "too_new"
    INVALID_FORMAT = "invalid_format"


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


def validate_timestamp_freshness(timestamp_str: str, ttl_seconds: int = 300) -> TimestampValidationResult:
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


# Keep the internal name for backward-compat within this package.
_validate_timestamp_freshness = validate_timestamp_freshness


def _hash_payload(payload: dict | None) -> str:
    """Return a stable SHA-256 hex digest of a payload dict.

    Keys are sorted so insertion order does not affect the hash.
    ``None`` and ``{}`` both hash to the same digest.
    """
    normalised = payload if payload else {}
    canonical = json.dumps(normalised, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _compute_approval_hmac(envelope_id: str, action: str, secret: str) -> str:
    """HMAC-SHA256 of 'approve:<envelope_id>:<action>' with *secret*.

    Pure computation — callers supply the secret explicitly so this function
    can be used in test fixtures as well as production paths.
    """
    message = f"approve:{envelope_id}:{action}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_approval_hmac(confirmation: object, envelope_id: str, action: str) -> bool:
    """Verify that *confirmation.approval_hmac* is bound to *envelope_id* and *action*.

    Loads the secret from ``APPROVAL_HMAC_SECRET`` (falls back to the dispatch
    signing secret so single-secret deployments work out of the box).

    Fail-closed policy (issue #925): when a signing secret IS configured, a
    confirmation that carries no ``approval_hmac`` is **rejected** — an attacker
    can otherwise submit approvals with no HMAC and have them accepted, defeating
    the envelope/action binding entirely. The legacy permissive behaviour (accept
    unsigned confirmations) is available only behind the explicit, default-off
    ``DISPATCH_ALLOW_UNSIGNED_APPROVAL`` flag, which logs a deprecation warning.

    When NO secret is configured the binding cannot be checked at all, so the
    result is ``False`` regardless (the dispatch path enforces other gates).

    ``confirmation`` is expected to be a ``DispatchConfirmation``-like object
    with an ``approval_hmac`` attribute.
    """
    secret = _resolve_approval_secret()
    stored_hmac: str = getattr(confirmation, "approval_hmac", "")

    if not stored_hmac:
        if not secret:
            # No secret configured → binding cannot be established. Fail closed.
            return False
        # Secret IS configured but the confirmation is unsigned.
        if os.environ.get(_ALLOW_UNSIGNED_APPROVAL_ENV, "").strip().lower() in _TRUTHY:
            log.warning(
                "DEPRECATED: accepting unsigned dispatch approval for envelope_id=%s "
                "action=%s because %s is set. This bypass will be removed; migrate "
                "clients to send an approval_hmac (issue #925).",
                envelope_id,
                action,
                _ALLOW_UNSIGNED_APPROVAL_ENV,
            )
            return True
        # Fail closed: a configured secret means approvals MUST be signed.
        return False

    if not secret:
        return False
    expected = _compute_approval_hmac(envelope_id, action, secret)
    return hmac.compare_digest(stored_hmac, expected)


def _build_canonical_json(
    action: str,
    source: str,
    target: str,
    requested_by: str,
    issued_at: str,
    envelope_version: int,
    principal: str = "",
    on_behalf_of: str = "",
    correlation_id: str = "",
) -> str:
    """Build canonical JSON string for signing/verification."""
    return json.dumps(
        {
            "action": action,
            "source": source,
            "target": target,
            "requested_by": requested_by,
            "issued_at": issued_at,
            "envelope_version": envelope_version,
            "principal": principal,
            "on_behalf_of": on_behalf_of,
            "correlation_id": correlation_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def sign_payload(canonical_json: str, secret: str) -> str:
    """Generate HMAC-SHA256 signature over a pre-built canonical JSON string.

    Reusable by Maxwell-Daemon to sign dashboard-issued envelopes.
    """
    return hmac.new(secret.encode(), canonical_json.encode(), hashlib.sha256).hexdigest()


def verify_payload(canonical_json: str, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature over a canonical JSON string.

    Reusable by Maxwell-Daemon to verify dashboard-issued envelopes.
    """
    expected = sign_payload(canonical_json, secret)
    return hmac.compare_digest(expected, signature)


def _sign_envelope_payload(
    action: str,
    source: str,
    target: str,
    requested_by: str,
    issued_at: str,
    envelope_version: int,
    secret: str,
    principal: str = "",
    on_behalf_of: str = "",
    correlation_id: str = "",
    payload_hash: str = "",
) -> str:
    """Generate HMAC-SHA256 signature over envelope payload.

    *payload_hash* (issue #317) is appended to the canonical JSON so that a
    captured envelope cannot be replayed with a substituted payload.
    """
    canonical = _build_canonical_json(
        action,
        source,
        target,
        requested_by,
        issued_at,
        envelope_version,
        principal,
        on_behalf_of,
        correlation_id,
    )
    # Include payload hash to bind signature to payload content (issue #317).
    if payload_hash:
        canonical = canonical + payload_hash
    return sign_payload(canonical, secret)


def _verify_envelope_signature(
    action: str,
    source: str,
    target: str,
    requested_by: str,
    issued_at: str,
    envelope_version: int,
    signature: str,
    secret: str,
    principal: str = "",
    on_behalf_of: str = "",
    correlation_id: str = "",
    payload_hash: str = "",
) -> bool:
    """Verify HMAC-SHA256 signature over envelope payload."""
    expected = _sign_envelope_payload(
        action,
        source,
        target,
        requested_by,
        issued_at,
        envelope_version,
        secret,
        principal,
        on_behalf_of,
        correlation_id,
        payload_hash,
    )
    return hmac.compare_digest(expected, signature)
