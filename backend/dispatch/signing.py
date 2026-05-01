"""Dispatch signing — HMAC-SHA256 payload signing and verification.

This module is reusable by both the dashboard and Maxwell-Daemon (via shared_scripts/)
for verifying dashboard-issued envelopes per the DRY rule in CLAUDE.md.

Public API:
- sign_payload(canonical_json, secret) -> str
- verify_payload(canonical_json, signature, secret) -> bool
- _load_signing_secret() -> str
- _sign_envelope_payload(...) -> str
- _verify_envelope_signature(...) -> bool
- validate_timestamp_freshness(timestamp_str, ttl_seconds) -> TimestampValidationResult
- TimestampValidationResult (enum)
"""

from __future__ import annotations

import datetime as _dt_mod
import enum
import hashlib
import hmac
import json
import os

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime


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
) -> str:
    """Generate HMAC-SHA256 signature over envelope payload."""
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
    )
    return hmac.compare_digest(expected, signature)
