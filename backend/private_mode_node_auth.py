"""Private Mode PM-18: zero-trust node identity, signed envelopes, and mutual auth (#3269)."""

from __future__ import annotations

import datetime as _dt_mod
import json
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from dispatch.signing import _hash_payload, sign_payload, verify_payload
from private_mode import (
    DISABLED,
    ENVELOPE_SCHEMA_VERSION,
    ClassificationEnvelope,
    ClassificationError,
    Level,
    PrivateModeConfig,
    propagate_envelope,
    stamp_envelope,
    validate_envelope,
)

NODE_ROLES: frozenset[str] = frozenset({"hub", "spoke"})
MIN_SECRET_LENGTH: int = 16
EXPORTS: frozenset[str] = frozenset(
    "MutualAuthResult NODE_ROLES NodeIdentity NodeTrustRegistry SignedNodeEnvelope "
    "build_mutual_node_exchange sign_node_envelope verify_mutual_node_call verify_node_envelope".split()
)
_WIRE_KEYS: frozenset[str] = frozenset(
    "envelope sender_node recipient_node key_id nonce issued_at payload_digest signature".split()
)


def _req_str(val: object, where: str) -> str:
    if not isinstance(val, str) or not val.strip():
        raise ClassificationError(f"{where}: must be a non-empty str")
    return val.strip()


@dataclass(frozen=True)
class NodeIdentity:
    """Per-node cryptographic identity (signing secret is excluded from ``repr``)."""

    node_id: str
    role: str
    key_id: str
    _signing_secret: str = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_id", _req_str(self.node_id, "NodeIdentity.node_id"))
        role = _req_str(self.role, "NodeIdentity.role")
        if role not in NODE_ROLES:
            raise ClassificationError("NodeIdentity.role: unsupported role (expected 'hub' or 'spoke')")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "key_id", _req_str(self.key_id, "NodeIdentity.key_id"))
        if not isinstance(self._signing_secret, str) or len(self._signing_secret.strip()) < MIN_SECRET_LENGTH:
            raise ClassificationError("NodeIdentity.signing_secret: must be a str of length >= 16")


@dataclass(frozen=True)
class SignedNodeEnvelope:
    """Signed wire envelope binding classification metadata, sender/recipient nodes, nonce, and payload digest."""

    envelope: ClassificationEnvelope
    sender_node: str
    recipient_node: str
    key_id: str
    nonce: str
    issued_at: float
    payload_digest: str
    signature: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "envelope": self.envelope.to_dict(),
            "sender_node": self.sender_node,
            "recipient_node": self.recipient_node,
            "key_id": self.key_id,
            "nonce": self.nonce,
            "issued_at": self.issued_at,
            "payload_digest": self.payload_digest,
            "signature": self.signature,
        }

    def canonical_json(self) -> str:
        data = {
            "schema_version": ENVELOPE_SCHEMA_VERSION,
            "envelope": self.envelope.to_dict(),
            "sender_node": self.sender_node,
            "recipient_node": self.recipient_node,
            "key_id": self.key_id,
            "nonce": self.nonce,
            "issued_at": round(float(self.issued_at), 6),
            "payload_digest": self.payload_digest,
        }
        return json.dumps(data, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_wire(cls, raw: SignedNodeEnvelope | Mapping[str, Any]) -> SignedNodeEnvelope:
        if isinstance(raw, SignedNodeEnvelope):
            return raw
        if not isinstance(raw, Mapping):
            raise ClassificationError("SignedNodeEnvelope: expected SignedNodeEnvelope or mapping")
        missing = sorted(_WIRE_KEYS - set(raw))
        if missing:
            raise ClassificationError(f"SignedNodeEnvelope: missing key(s): {', '.join(missing)}")
        ts = raw["issued_at"]
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            raise ClassificationError("SignedNodeEnvelope: issued_at must be numeric")
        return cls(
            envelope=validate_envelope(raw["envelope"]),
            sender_node=_req_str(raw["sender_node"], "SignedNodeEnvelope.sender_node"),
            recipient_node=_req_str(raw["recipient_node"], "SignedNodeEnvelope.recipient_node"),
            key_id=_req_str(raw["key_id"], "SignedNodeEnvelope.key_id"),
            nonce=_req_str(raw["nonce"], "SignedNodeEnvelope.nonce"),
            issued_at=float(ts),
            payload_digest=_req_str(raw["payload_digest"], "SignedNodeEnvelope.payload_digest"),
            signature=str(raw["signature"]).strip() if isinstance(raw["signature"], str) else "",
        )


@dataclass(frozen=True)
class MutualAuthResult:
    """Verified mutual node↔hub authentication outcome."""

    authenticated: bool
    envelope: ClassificationEnvelope
    caller_node: str
    peer_node: str


class NodeTrustRegistry:
    """Per-node identity registry and bounded replay-nonce cache for node↔hub zero trust."""

    def __init__(self, *, ttl_seconds: float = 300.0, max_nonces: int = 4096) -> None:
        if ttl_seconds <= 0 or max_nonces < 1:
            raise ClassificationError("NodeTrustRegistry: ttl_seconds and max_nonces must be positive")
        self.ttl_seconds, self._max_nonces = float(ttl_seconds), int(max_nonces)
        self._lock = threading.RLock()
        self._nodes: dict[str, NodeIdentity] = {}
        self._seen_nonces: OrderedDict[tuple[str, str], float] = OrderedDict()

    def register_node(self, identity: NodeIdentity) -> None:
        if not isinstance(identity, NodeIdentity):
            raise ClassificationError("register_node: identity must be a NodeIdentity")
        with self._lock:
            self._nodes[identity.node_id] = identity

    def get_node(self, node_id: str) -> NodeIdentity:
        nid = _req_str(node_id, "get_node.node_id")
        with self._lock:
            ident = self._nodes.get(nid)
            if ident is None:
                raise ClassificationError("verify_node_envelope: spoofed or unregistered node is refused")
            return ident

    def check_and_record_nonce(self, sender_node: str, nonce: str, *, now: float) -> None:
        key = (sender_node, nonce)
        with self._lock:
            cutoff = now - self.ttl_seconds
            for k in [k for k, ts in self._seen_nonces.items() if ts < cutoff]:
                self._seen_nonces.pop(k, None)
            if key in self._seen_nonces:
                raise ClassificationError("verify_node_envelope: replayed envelope is rejected")
            self._seen_nonces[key] = now
            while len(self._seen_nonces) > self._max_nonces:
                self._seen_nonces.popitem(last=False)


def sign_node_envelope(
    envelope: ClassificationEnvelope | Mapping[str, Any],
    *,
    sender_identity: NodeIdentity,
    recipient_node: str,
    payload: Mapping[str, Any] | None = None,
    nonce: str | None = None,
    issued_at: float | None = None,
    config: PrivateModeConfig = DISABLED,
) -> SignedNodeEnvelope:
    """Sign a :class:`ClassificationEnvelope` behind the unified ``dispatch.signing`` interface."""
    if not isinstance(sender_identity, NodeIdentity) or not isinstance(config, PrivateModeConfig):
        raise ClassificationError("sign_node_envelope: invalid sender_identity or config")
    recip = _req_str(recipient_node, "sign_node_envelope.recipient_node")
    val_env = (
        stamp_envelope(Level.PUBLIC, hop="flag_off", config=config)
        if not config.enabled
        else validate_envelope(envelope)
    )
    eff_nonce = _req_str(nonce or f"pmnonce-{secrets.token_hex(8)}", "sign_node_envelope.nonce")
    eff_ts = float(time.time() if issued_at is None else issued_at)
    digest = _hash_payload(dict(payload) if payload is not None else {})
    unsigned = SignedNodeEnvelope(
        val_env, sender_identity.node_id, recip, sender_identity.key_id, eff_nonce, eff_ts, digest, ""
    )
    sig = sign_payload(unsigned.canonical_json(), sender_identity._signing_secret)
    return SignedNodeEnvelope(
        val_env, sender_identity.node_id, recip, sender_identity.key_id, eff_nonce, eff_ts, digest, sig
    )


def verify_node_envelope(
    signed: SignedNodeEnvelope | Mapping[str, Any],
    *,
    registry: NodeTrustRegistry,
    expected_recipient: str,
    claimed_sender: str | None = None,
    payload: Mapping[str, Any] | None = None,
    now: float | None = None,
    config: PrivateModeConfig = DISABLED,
) -> ClassificationEnvelope:
    """Verify per-node signature, freshness, and replay protection; propagate hop on success."""
    if not isinstance(registry, NodeTrustRegistry) or not isinstance(config, PrivateModeConfig):
        raise ClassificationError("verify_node_envelope: invalid registry or config")
    recip = _req_str(expected_recipient, "verify_node_envelope.expected_recipient")
    if not config.enabled:
        return stamp_envelope(Level.PUBLIC, hop="flag_off", config=config)
    wire = SignedNodeEnvelope.from_wire(signed)
    if not wire.signature:
        raise ClassificationError("verify_node_envelope: unsigned envelope is rejected")
    if wire.recipient_node != recip or (claimed_sender is not None and claimed_sender.strip() != wire.sender_node):
        raise ClassificationError("verify_node_envelope: spoofed sender or recipient mismatch is refused")
    sender_id = registry.get_node(wire.sender_node)
    if wire.key_id != sender_id.key_id:
        raise ClassificationError("verify_node_envelope: spoofed key_id mismatch is refused")
    if wire.payload_digest != _hash_payload(dict(payload) if payload is not None else {}):
        raise ClassificationError("verify_node_envelope: payload digest mismatch is rejected")
    if not verify_payload(wire.canonical_json(), wire.signature, sender_id._signing_secret):
        raise ClassificationError("verify_node_envelope: spoofed node signature verification failed")
    check_now = float(time.time() if now is None else now)
    if abs(check_now - wire.issued_at) > registry.ttl_seconds:
        raise ClassificationError("verify_node_envelope: expired or future-dated envelope is rejected")
    registry.check_and_record_nonce(wire.sender_node, wire.nonce, now=check_now)
    return propagate_envelope(wire.envelope, hop=f"node:{wire.sender_node}->{wire.recipient_node}")


def _audit(
    actor: str, target_id: str, level: Level, outcome: str, meta: Mapping[str, str], cfg: PrivateModeConfig, store: Any
) -> None:
    if not cfg.enabled and store is None:
        return
    if not hasattr(_dt_mod, "UTC"):
        _dt_mod.UTC = _dt_mod.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017
    from private_context_audit import PrivateModeAuditEvent, get_audit_store  # noqa: PLC0415

    res = (store or get_audit_store()).record_decision(
        PrivateModeAuditEvent(
            action="node.mutual_auth", actor=actor, target_id=target_id, level=level, outcome=outcome, metadata=meta
        ),
        config=cfg,
    )
    if cfg.enabled and (res.parked or res.state != "OK"):
        raise ClassificationError("verify_mutual_node_call: audit write failed (fail-closed)")


def build_mutual_node_exchange(
    envelope: ClassificationEnvelope | Mapping[str, Any],
    *,
    caller_identity: NodeIdentity,
    peer_identity: NodeIdentity,
    request_payload: Mapping[str, Any] | None = None,
    request_nonce: str | None = None,
    response_nonce: str | None = None,
    issued_at: float | None = None,
    config: PrivateModeConfig = DISABLED,
) -> tuple[SignedNodeEnvelope, SignedNodeEnvelope]:
    """Construct a mutually signed caller->peer request and peer->caller ack binding ``request_nonce``."""
    common: dict[str, Any] = {"issued_at": issued_at, "config": config}
    req = sign_node_envelope(
        envelope,
        sender_identity=caller_identity,
        recipient_node=peer_identity.node_id,
        payload=request_payload,
        nonce=request_nonce,
        **common,
    )
    resp = sign_node_envelope(
        req.envelope,
        sender_identity=peer_identity,
        recipient_node=caller_identity.node_id,
        payload={"request_nonce": req.nonce, "ack": True},
        nonce=response_nonce,
        **common,
    )
    return req, resp


def verify_mutual_node_call(
    request_envelope: SignedNodeEnvelope | Mapping[str, Any],
    response_envelope: SignedNodeEnvelope | Mapping[str, Any] | None,
    *,
    registry: NodeTrustRegistry,
    caller_node: str,
    peer_node: str,
    request_payload: Mapping[str, Any] | None = None,
    target_id: str = "node-mutual-call",
    now: float | None = None,
    config: PrivateModeConfig = DISABLED,
    audit_store: Any | None = None,
) -> MutualAuthResult:
    """Enforce mutual node↔hub authentication on calls carrying SENSITIVE metadata."""
    caller, peer = _req_str(caller_node, "caller_node"), _req_str(peer_node, "peer_node")
    if not isinstance(config, PrivateModeConfig):
        raise ClassificationError("verify_mutual_node_call: config must be a PrivateModeConfig")
    if not config.enabled:
        return MutualAuthResult(True, stamp_envelope(Level.PUBLIC, hop="flag_off", config=config), caller, peer)
    meta = {"caller_node": caller, "peer_node": peer}
    if {registry.get_node(caller).role, registry.get_node(peer).role} != {"hub", "spoke"}:
        _audit(caller, target_id, Level.SENSITIVE, "refused-non-hub-peer", meta, config, audit_store)
        raise ClassificationError("verify_mutual_node_call: mutual node↔hub auth requires one hub and one spoke")
    req_wire = SignedNodeEnvelope.from_wire(request_envelope)
    common: dict[str, Any] = {"registry": registry, "now": now, "config": config}
    verified_req = verify_node_envelope(
        req_wire, expected_recipient=peer, claimed_sender=caller, payload=request_payload, **common
    )
    if response_envelope is None:
        _audit(caller, target_id, verified_req.level, "refused-missing-peer-ack", meta, config, audit_store)
        raise ClassificationError("verify_mutual_node_call: missing mutual peer authentication envelope")
    ack_payload = {"request_nonce": req_wire.nonce, "ack": True}
    verified_resp = verify_node_envelope(
        response_envelope, expected_recipient=caller, claimed_sender=peer, payload=ack_payload, **common
    )
    _audit(caller, target_id, verified_resp.level, "mutual-auth-ok", meta, config, audit_store)
    return MutualAuthResult(True, verified_resp, caller, peer)
