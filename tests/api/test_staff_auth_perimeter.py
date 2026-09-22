"""Auth perimeter for /api/staff/* (epic #1192).

``/api/staff/`` is listed in ``_ALT_AUTH_EXEMPT_PREFIXES`` so the structural
perimeter does not force-401 fleet-token callers; these tests prove the route
dependencies themselves close the door: mutations need an orchestrator peer,
reads need a fleet peer once ``HUB_FLEET_TOKEN`` is configured.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.9", 51000)


@pytest.fixture
def remote_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[TestClient]:
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-secret-token")
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "no-roles"))
    from staff import runner as runner_mod  # noqa: PLC0415
    from staff import store as store_mod  # noqa: PLC0415

    store_mod.reset_store()
    runner_mod.reset_runner()
    from server import app  # noqa: PLC0415

    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE)
    store_mod.reset_store()
    runner_mod.reset_runner()


@pytest.mark.unit
def test_remote_unauthenticated_dispatch_is_rejected(remote_client: TestClient) -> None:
    resp = remote_client.post("/api/staff/ad-hoc/run", json={"prompt": "x", "dry_run": True}, headers=_XHR)
    assert resp.status_code == 401


@pytest.mark.unit
def test_remote_unauthenticated_cancel_is_rejected(remote_client: TestClient) -> None:
    assert remote_client.post("/api/staff/runs/run-x/cancel", headers=_XHR).status_code == 401


@pytest.mark.unit
def test_remote_read_requires_fleet_token_when_configured(remote_client: TestClient) -> None:
    assert remote_client.get("/api/staff/roster").status_code == 401
    assert remote_client.get("/api/staff/board").status_code == 401
    ok = remote_client.get("/api/staff/roster", headers={"Authorization": "Bearer fleet-secret-token"})
    assert ok.status_code == 200
    assert "roles" in ok.json()


@pytest.mark.unit
def test_fleet_token_can_dry_run_dispatch(remote_client: TestClient) -> None:
    resp = remote_client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "preview only", "provider": "claude", "dry_run": True},
        headers={**_XHR, "Authorization": "Bearer fleet-secret-token"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["dry_run"] is True
