"""Regression tests for issue #919 — dispatch HMAC signature gate.

An attacker who POSTs a signature-less (or empty-signature) envelope to
``/api/fleet/dispatch/submit`` must be rejected. The server must never mint a
signature on the caller's behalf during inbound parsing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND = str(Path(__file__).resolve().parents[2] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

pytestmark = pytest.mark.skipif(sys.version_info < (3, 11), reason="dispatch_contract requires Python 3.11+")

_SECRET = "test-dispatch-secret-919"  # pragma: allowlist secret


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DISPATCH_SIGNING_SECRET", _SECRET)
    from routers import dispatch  # noqa: PLC0415

    app = FastAPI()
    app.include_router(dispatch.router)
    return TestClient(app)


def _valid_envelope_dict() -> dict:
    """A correctly signed, allowlisted envelope minted locally."""
    import dispatch_contract  # noqa: PLC0415

    action = next(iter(dispatch_contract.ALLOWLISTED_ACTIONS.values()))
    env = dispatch_contract.build_envelope(
        action=action.name,
        source="hub",
        target="node-1",
        requested_by="operator",
    )
    return env.to_dict()


def test_from_dict_never_mints_signature() -> None:
    """Issue #919: parsing a wire body with no signature must not auto-sign."""
    os.environ["DISPATCH_SIGNING_SECRET"] = _SECRET
    import dispatch_contract  # noqa: PLC0415

    data = _valid_envelope_dict()
    data["signature"] = ""

    parsed = dispatch_contract.CommandEnvelope.from_dict(data)

    assert parsed.signature == "", "from_dict must leave an absent wire signature empty"
    assert parsed.signature_authentic is False
    crypto = dispatch_contract.validate_envelope_crypto(parsed)
    assert crypto.valid is False
    assert "signature missing" in crypto.reason


def test_from_dict_preserves_wire_signature_verbatim() -> None:
    """A provided wire signature is preserved exactly, never re-minted."""
    os.environ["DISPATCH_SIGNING_SECRET"] = _SECRET
    import dispatch_contract  # noqa: PLC0415

    data = _valid_envelope_dict()
    data["signature"] = "f" * 64  # attacker-supplied garbage

    parsed = dispatch_contract.CommandEnvelope.from_dict(data)

    assert parsed.signature == "f" * 64
    # Garbage signature is authentic-as-present but fails verification.
    assert parsed.signature_authentic is True
    crypto = dispatch_contract.validate_envelope_crypto(parsed)
    assert crypto.valid is False
    assert "invalid" in crypto.reason


def test_submit_rejects_missing_signature(client: TestClient) -> None:
    """POST with the signature field omitted entirely is rejected (not 2xx)."""
    data = _valid_envelope_dict()
    data.pop("signature", None)

    resp = client.post("/api/fleet/dispatch/submit", json=data)

    assert resp.status_code in (400, 401, 403), resp.text
    assert "signature missing" in resp.text


def test_submit_rejects_empty_signature(client: TestClient) -> None:
    """POST with an empty-string signature is rejected."""
    data = _valid_envelope_dict()
    data["signature"] = ""

    resp = client.post("/api/fleet/dispatch/submit", json=data)

    assert resp.status_code in (400, 401, 403), resp.text
    assert "signature missing" in resp.text


def test_submit_rejects_wrong_signature(client: TestClient) -> None:
    """POST with an incorrect signature is rejected."""
    data = _valid_envelope_dict()
    data["signature"] = "a" * 64

    resp = client.post("/api/fleet/dispatch/submit", json=data)

    assert resp.status_code in (400, 401, 403), resp.text
    assert "invalid" in resp.text


def test_submit_accepts_correctly_signed_envelope(client: TestClient) -> None:
    """A locally minted, correctly signed envelope still round-trips."""
    data = _valid_envelope_dict()

    resp = client.post("/api/fleet/dispatch/submit", json=data)

    # Accepted at the crypto gate. Non-privileged allowlisted action should
    # pass validation and return the prototype command.
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action"] == data["action"]
