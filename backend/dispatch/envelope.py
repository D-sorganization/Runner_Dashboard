"""Dispatch envelope schema — CommandEnvelope, DispatchConfirmation, schema constants.

This module is intentionally side-effect free so it can be exercised in tests
without touching the running dashboard service.
"""

from __future__ import annotations

import datetime as _dt_mod
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from dispatch.signing import _load_signing_secret, _sign_envelope_payload, _verify_envelope_signature
from time_utils import utc_now_iso

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017

SCHEMA_VERSION = "dispatch-envelope.v1"
ENVELOPE_VERSION = 1
MIN_ENVELOPE_VERSION = 1
MAX_ENVELOPE_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({"dispatch-envelope.v1"})


def _utc_now() -> _dt_mod.datetime:
    return _dt_mod.datetime.now(UTC)


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
    issued_at: str = field(default_factory=utc_now_iso)
    signature: str = ""
    principal: str = ""
    on_behalf_of: str = ""
    correlation_id: str = ""

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
                self.principal,
                self.on_behalf_of,
                self.correlation_id,
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
            issued_at=str(data.get("issued_at", utc_now_iso())),
            signature=str(data.get("signature", "")),
            principal=str(data.get("principal", "")),
            on_behalf_of=str(data.get("on_behalf_of", "")),
            correlation_id=str(data.get("correlation_id", "")),
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
                self.principal,
                self.on_behalf_of,
                self.correlation_id,
            )
        except Exception:  # justified: broad guard; verify returns False on any signing failure
            return False
