"""Coordination API for shared provider-down markers (#4564)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("fcntl")
import identity
from coordination_fake_rm import FakeRM, install
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_CODEX = {"Authorization": "Bearer codex-token", **_XHR}


@pytest.fixture
def marker_rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRM:
    principals = {
        "codex-token": identity.Principal(
            id="agent-codex",
            type="bot",
            name="agent-codex",
            roles=["bot"],
            scopes=["fleet.read", "coordination.write"],
        ),
    }
    yield from install(tmp_path, monkeypatch, principals, repos="Runner_Dashboard")


@pytest.fixture
def marker_api(tmp_path: Path, marker_rm: FakeRM, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path / "rd-state"))
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=("100.64.0.9", 51000))


def test_provider_markers_set_get_clear(marker_api: TestClient) -> None:
    empty = marker_api.get("/api/coordination/provider-markers", headers=_CODEX)
    assert empty.status_code == 200
    assert empty.json()["markers"] == []

    created = marker_api.post(
        "/api/coordination/provider-markers",
        headers=_CODEX,
        json={"provider": "claude", "kind": "rate_limited", "ttl_sec": 90, "reason": "hub probe"},
    )
    assert created.status_code == 200
    marker = created.json()["marker"]
    assert marker["provider"] == "claude"
    assert marker["kind"] == "rate_limited"

    listed = marker_api.get("/api/coordination/provider-markers", headers=_CODEX)
    assert listed.status_code == 200
    assert len(listed.json()["markers"]) == 1

    cleared = marker_api.delete("/api/coordination/provider-markers/claude", headers=_CODEX)
    assert cleared.status_code == 200
    assert cleared.json()["removed"] == 1
    assert marker_api.get("/api/coordination/provider-markers", headers=_CODEX).json()["markers"] == []


def test_provider_marker_restore_endpoint(marker_api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    past = time.time() - 120.0
    future = time.time() + 3600.0
    marker_api.post(
        "/api/coordination/provider-markers",
        headers=_CODEX,
        json={"provider": "claude", "kind": "provider_down", "until": future},
    )
    marker_api.post(
        "/api/coordination/provider-markers",
        headers=_CODEX,
        json={"provider": "codex", "kind": "rate_limited", "until": past},
    )
    monkeypatch.setattr(
        "coordination.provider_marker_restore._default_provider_healthy",
        lambda prov: prov == "claude",
    )

    resp = marker_api.post("/api/coordination/provider-markers/restore", headers=_CODEX, json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["store_ok"] is True
    assert body["cleared_total"] >= 2
    assert marker_api.get("/api/coordination/provider-markers", headers=_CODEX).json()["markers"] == []
