"""Intra-fleet authentication tests for hub-reachable fleet routes (issue #922).

Before #922 the HUB_FLEET_TOKEN existed only in documentation and as a header
spokes sent into the void — the hub never validated it, and proxied fleet-read
routes had no auth dependency at all, so the fleet trust boundary did not exist
in code.

`require_fleet_peer` enforces: when HUB_FLEET_TOKEN is set, a caller must present
either a valid principal OR `Authorization: Bearer <HUB_FLEET_TOKEN>` (constant-
time compare); when the token is unset, fleet reads stay tailnet-public.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

from fastapi import HTTPException  # noqa: E402
from identity import Principal, require_fleet_peer  # noqa: E402


def _request(*, session: dict | None = None) -> MagicMock:
    req = MagicMock()
    req.session = session if session is not None else {}
    return req


# ── No token configured → tailnet-public (no-op) ─────────────────────────────


def test_no_token_configured_allows_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)
    label = require_fleet_peer(_request(), header_token=None)
    assert label == "anonymous:tailnet"


# ── Token configured → unauthenticated caller rejected ───────────────────────


def test_token_configured_rejects_missing_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "the-fleet-token")  # pragma: allowlist secret
    with pytest.raises(HTTPException) as exc:
        require_fleet_peer(_request(), header_token=None)
    assert exc.value.status_code == 401


def test_token_configured_rejects_wrong_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "the-fleet-token")  # pragma: allowlist secret
    with pytest.raises(HTTPException) as exc:
        require_fleet_peer(_request(), header_token="Bearer wrong-token")
    assert exc.value.status_code == 401


# ── Token configured → correct fleet token accepted ──────────────────────────


def test_token_configured_accepts_matching_fleet_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "the-fleet-token")  # pragma: allowlist secret
    label = require_fleet_peer(_request(), header_token="Bearer the-fleet-token")
    assert label == "fleet-peer"


# ── A valid principal is always accepted (even with token set) ───────────────


def test_valid_principal_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "the-fleet-token")  # pragma: allowlist secret
    import identity

    prin = Principal(id="alice", type="human", name="Alice", roles=["operator"])
    monkeypatch.setitem(identity.identity_manager.principals, "alice", prin)
    monkeypatch.setattr(identity.sm, "touch_session", lambda sid: True)

    req = _request(session={"principal_id": "alice", "session_id": "sess1"})
    label = require_fleet_peer(req, header_token=None)
    assert label == "principal:alice"


def test_valid_service_token_principal_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "the-fleet-token")  # pragma: allowlist secret
    import identity

    prin = Principal(id="bot", type="bot", name="Bot", roles=["bot"])
    monkeypatch.setattr(identity.identity_manager, "verify_token", lambda raw: prin if raw == "svc-tok" else None)

    label = require_fleet_peer(_request(), header_token="Bearer svc-tok")
    assert label == "principal:bot"


# ── Uses constant-time comparison (hmac.compare_digest) ──────────────────────


def test_uses_constant_time_comparison() -> None:
    """The implementation must use hmac.compare_digest, not == (timing-safe)."""
    src = (_BACKEND / "identity.py").read_text(encoding="utf-8")
    assert "hmac.compare_digest" in src, "fleet token comparison must use hmac.compare_digest (#922)"


# ── HTTP-level: the /api/fleet/status route is gated ─────────────────────────


def test_fleet_status_route_returns_401_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: with HUB_FLEET_TOKEN set, GET /api/fleet/status without
    credentials returns 401."""
    monkeypatch.setenv("HUB_FLEET_TOKEN", "the-fleet-token")  # pragma: allowlist secret
    # Force local serving (not proxy) so the gate is what we exercise.
    monkeypatch.setenv("MACHINE_ROLE", "hub")

    import routers.fleet as fleet_mod
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.middleware.sessions import SessionMiddleware

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")  # pragma: allowlist secret
    app.include_router(fleet_mod.router)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/api/fleet/status")
    assert resp.status_code == 401


def test_fleet_status_route_accepts_fleet_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "the-fleet-token")  # pragma: allowlist secret
    monkeypatch.setenv("MACHINE_ROLE", "hub")

    import routers.fleet as fleet_mod
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.middleware.sessions import SessionMiddleware

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")  # pragma: allowlist secret
    app.include_router(fleet_mod.router)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/api/fleet/status", headers={"Authorization": "Bearer the-fleet-token"})
    # Not 401/403 — the auth gate passed (handler may still 5xx on gh calls in
    # this minimal harness, but the perimeter is what we assert).
    assert resp.status_code not in (401, 403)
