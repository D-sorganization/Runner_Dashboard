"""Contract tests for Private Mode PM-18 zero-trust node auth (issue #3269, epic #3249)."""

from __future__ import annotations

import dataclasses
import datetime as _dt_mod
import sys
from pathlib import Path
from typing import Any

import pytest

if not hasattr(_dt_mod, "UTC"):
    _dt_mod.UTC = _dt_mod.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import private_context_audit as pca  # noqa: E402
import private_mode as pm  # noqa: E402
import private_mode_node_auth as pmna  # noqa: E402

EXAMPLE_TARGET = "example-corp/widgets-sensitive#18"
HUB_SECRET = "example-corp-hub-signing-secret-0001"
SPOKE_A_SECRET = "example-corp-spoke-a-signing-secret-1"
SPOKE_B_SECRET = "example-corp-spoke-b-signing-secret-2"
ENABLED = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)
PAYLOAD = {"target_id": EXAMPLE_TARGET, "action": "status_sync"}


def _registry() -> tuple[pmna.NodeTrustRegistry, pmna.NodeIdentity, pmna.NodeIdentity, pmna.NodeIdentity]:
    reg = pmna.NodeTrustRegistry(ttl_seconds=300.0, max_nonces=128)
    hub = pmna.NodeIdentity("node-hub", "hub", "kid-hub-v1", HUB_SECRET)
    spoke_a = pmna.NodeIdentity("node-spoke-a", "spoke", "kid-spoke-a-v1", SPOKE_A_SECRET)
    spoke_b = pmna.NodeIdentity("node-spoke-b", "spoke", "kid-spoke-b-v1", SPOKE_B_SECRET)
    for ident in (hub, spoke_a, spoke_b):
        reg.register_node(ident)
    return reg, hub, spoke_a, spoke_b


def _sign(sender: pmna.NodeIdentity, *, cfg: pm.PrivateModeConfig = ENABLED, **kw: Any) -> pmna.SignedNodeEnvelope:
    env = pm.stamp_envelope(pm.Level.SENSITIVE, hop="origin", config=cfg)
    defaults: dict[str, Any] = {
        "sender_identity": sender,
        "recipient_node": "node-hub",
        "payload": PAYLOAD,
        "issued_at": 1_700_000_000.0,
        "config": cfg,
    }
    defaults.update(kw)
    return pmna.sign_node_envelope(env, **defaults)


def test_pm18_unsigned_or_replayed_envelope_is_rejected() -> None:
    reg, _hub, spoke_a, _spoke_b = _registry()
    signed = _sign(spoke_a, nonce="nonce-0001")
    vkw: dict[str, Any] = {"registry": reg, "expected_recipient": "node-hub", "payload": PAYLOAD, "config": ENABLED}

    with pytest.raises(pm.ClassificationError, match="unsigned"):
        pmna.verify_node_envelope({**signed.to_dict(), "signature": ""}, now=1_700_000_010.0, **vkw)
    with pytest.raises(pm.ClassificationError, match="expired"):
        pmna.verify_node_envelope(signed, now=1_700_000_500.0, **vkw)

    verified = pmna.verify_node_envelope(signed, now=1_700_000_010.0, **vkw)
    assert (verified.level, verified.hops[-1]) == (pm.Level.SENSITIVE, "node:node-spoke-a->node-hub")

    with pytest.raises(pm.ClassificationError, match="replay"):
        pmna.verify_node_envelope(signed, now=1_700_000_015.0, **vkw)


def test_pm18_spoofed_node_is_refused() -> None:
    reg, _hub, spoke_a, spoke_b = _registry()
    vkw: dict[str, Any] = {
        "registry": reg,
        "expected_recipient": "node-hub",
        "payload": PAYLOAD,
        "now": 1_700_000_005.0,
        "config": ENABLED,
    }
    intruder = pmna.NodeIdentity("node-intruder", "spoke", "kid-intruder", "example-corp-intruder-secret-9999")
    with pytest.raises(pm.ClassificationError, match="spoofed|unregistered"):
        pmna.verify_node_envelope(_sign(intruder), **vkw)

    spoofed_as_a = dataclasses.replace(_sign(spoke_b), sender_node="node-spoke-a", key_id=spoke_a.key_id)
    with pytest.raises(pm.ClassificationError, match="spoofed"):
        pmna.verify_node_envelope(spoofed_as_a, **vkw)

    with pytest.raises(pm.ClassificationError, match="spoofed"):
        pmna.verify_node_envelope(_sign(spoke_a), claimed_sender="node-spoke-b", **vkw)


def test_pm18_mutual_auth_on_node_hub_calls_carrying_sensitive_metadata(tmp_path: Path) -> None:
    reg, hub, spoke_a, spoke_b = _registry()
    store = pca.PrivateContextAuditStore(tmp_path / "audit.sqlite3", checkpoint_path=tmp_path / "audit.checkpoint.json")
    env = pm.stamp_envelope(pm.Level.SENSITIVE, hop="origin", config=ENABLED)
    ex_kw: dict[str, Any] = {"request_payload": PAYLOAD, "issued_at": 1_700_000_000.0, "config": ENABLED}
    v_kw: dict[str, Any] = {
        "registry": reg,
        "caller_node": "node-spoke-a",
        "request_payload": PAYLOAD,
        "target_id": EXAMPLE_TARGET,
        "now": 1_700_000_005.0,
        "config": ENABLED,
        "audit_store": store,
    }

    req, resp = pmna.build_mutual_node_exchange(env, caller_identity=spoke_a, peer_identity=hub, **ex_kw)
    res = pmna.verify_mutual_node_call(req, resp, peer_node="node-hub", **v_kw)
    assert (res.authenticated, res.envelope.level, res.caller_node, res.peer_node) == (
        True,
        pm.Level.SENSITIVE,
        "node-spoke-a",
        "node-hub",
    )

    bad_req, bad_resp = pmna.build_mutual_node_exchange(env, caller_identity=spoke_a, peer_identity=spoke_b, **ex_kw)
    with pytest.raises(pm.ClassificationError, match="mutual|hub"):
        pmna.verify_mutual_node_call(bad_req, bad_resp, peer_node="node-spoke-b", **v_kw)
    assert [d.outcome for d in store.list_decisions()] == ["mutual-auth-ok", "refused-non-hub-peer"]
    assert store.verify_chain().ok is True


def test_pm18_flag_off_behaviour_unchanged_and_secret_redacted() -> None:
    reg, hub, spoke, _spoke_b = _registry()
    assert HUB_SECRET not in repr(hub)
    with pytest.raises(pm.ClassificationError, match="role"):
        pmna.NodeIdentity("node-x", "invalid", "kid-x", HUB_SECRET)
    with pytest.raises(pm.ClassificationError, match="signing_secret"):
        pmna.NodeIdentity("node-x", "spoke", "kid-x", "short")
    assert (pm.NodeIdentity, pm.NodeTrustRegistry) == (pmna.NodeIdentity, pmna.NodeTrustRegistry)

    signed = _sign(spoke, cfg=pm.DISABLED)
    unsigned_wire = {**signed.to_dict(), "signature": ""}
    verified = pmna.verify_node_envelope(
        unsigned_wire, registry=reg, expected_recipient="node-hub", payload=PAYLOAD, config=pm.DISABLED
    )
    mutual = pmna.verify_mutual_node_call(
        unsigned_wire, None, registry=reg, caller_node="node-spoke-a", peer_node="node-hub", config=pm.DISABLED
    )
    assert (signed.envelope.level, verified.level, mutual.authenticated, mutual.envelope.level) == (
        pm.Level.PUBLIC,
        pm.Level.PUBLIC,
        True,
        pm.Level.PUBLIC,
    )
