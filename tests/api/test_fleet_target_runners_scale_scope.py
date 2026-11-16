"""Least-privilege ``runners.scale`` for POST /api/fleet/target (#2889)."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import runner_target_contract as rtc
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import (
    Principal,
    principal_has_scope,
    require_any_scope,
    require_fleet_peer,
    require_principal,
    require_scope,
)
from routers import fleet_target_routes as routes
from routers import orchestration_schedule_routes as sched

URL = "/api/fleet/target"
_TARGET_AUTH = require_any_scope("runners.scale", "fleet.control")
FLEET_CONTROL_URL = "/api/fleet/maintenance"


class FakeRun:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='{"desired":2,"reason":"ok","busy":0}', stderr="")


def _scale_principal() -> Principal:
    return Principal(
        id="scale-only",
        type="bot",
        name="Scale",
        roles=["runner-scaler"],
        scopes=["runners.scale"],
    )


def _fleet_control_table_routes() -> list[tuple[str, str, dict[str, object]]]:
    return [
        ("POST", "/api/fleet/control/all-up?local=1", {}),
        ("POST", "/api/fleet/orchestration/deploy", {"host": "local", "runner": "r1"}),
        ("POST", "/api/fleet/schedule", {"enabled": True}),
        ("POST", "/api/fleet/maintenance", {"action": "drain"}),
    ]


@pytest.fixture
def target_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    override = tmp_path / "target-override.json"
    monkeypatch.setattr(rtc, "TARGET_OVERRIDE_PATH", override)
    scheduler = tmp_path / "runner-scheduler"
    scheduler.write_text("#!/bin/sh\n", encoding="utf-8")
    scheduler.chmod(0o755)
    sched._runner_scheduler_bin = str(scheduler)
    sched._runner_schedule_config = tmp_path / "schedule.json"
    sched._runner_scheduler_state = tmp_path / "state.json"
    sched._runner_base_dir = tmp_path
    sched._runner_schedule_config.write_text(
        '{"enabled":true,"timezone":"UTC","default_count":2,"max_count":6,"schedules":[]}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(routes, "_host_matches_local", lambda _host: True)
    fake = FakeRun()
    monkeypatch.setattr(routes.subprocess, "run", fake)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[_TARGET_AUTH] = _scale_principal
    app.dependency_overrides[require_fleet_peer] = lambda: None
    return TestClient(app, raise_server_exceptions=False)


def test_scale_only_principal_can_set_target_within_cap(target_client: TestClient) -> None:
    until = (datetime.now(UTC) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    response = target_client.post(
        URL,
        json={
            "host": "hub-a",
            "runners": 3,
            "until": until,
            "reason": "pytest scale",
            "apply": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runners"] == 3
    assert "clamped_to" not in body or body.get("clamped_to") == 6


def test_scale_only_rejects_over_cap(target_client: TestClient) -> None:
    until = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    response = target_client.post(
        URL,
        json={"host": "hub-a", "runners": 99, "until": until, "reason": "too high", "apply": False},
    )
    assert response.status_code == 400
    assert rtc.load_target_override() is None


@pytest.mark.parametrize(
    "payload",
    [
        {"host": "hub-a", "runners": 2, "reason": "no until"},
        {"host": "hub-a", "runners": 2, "until": "2029-01-01T00:00:00Z", "reason": "far"},
        {"host": "hub-a", "runners": 2, "until": "2099-01-01T00:00:00Z", "reason": "far"},
        {"host": "hub-a", "runners": 2, "until": None, "reason": "missing"},
    ],
)
def test_scale_only_rejects_bad_until(
    target_client: TestClient, payload: dict[str, object]
) -> None:
    if "until" not in payload:
        payload["until"] = None
    payload.setdefault("apply", False)
    response = target_client.post(URL, json=payload)
    assert response.status_code == 400
    assert rtc.load_target_override() is None


def test_scale_only_rejects_empty_reason(target_client: TestClient) -> None:
    until = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    response = target_client.post(
        URL,
        json={"host": "hub-a", "runners": 2, "until": until, "reason": "", "apply": False},
    )
    assert response.status_code == 400


def test_no_target_scope_receives_403_and_writes_no_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override = tmp_path / "target-override.json"
    monkeypatch.setattr(rtc, "TARGET_OVERRIDE_PATH", override)
    app = FastAPI()
    app.include_router(routes.router)
    viewer = Principal(id="viewer", type="human", name="Viewer", roles=["viewer"], scopes=["fleet.read"])
    app.dependency_overrides[require_principal] = lambda: viewer
    app.dependency_overrides[require_fleet_peer] = lambda: None
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(URL, json={"host": "hub-a", "runners": 1})
    assert response.status_code == 403
    assert not override.exists()


def test_staff_runner_scale_token_grants_only_runners_scale_among_fleet_scopes() -> None:
    from staff.tokens import mint_run_token, resolve_run_scopes

    scopes = resolve_run_scopes(["runner.scale"])
    assert scopes == ["runners.scale"]
    raw = mint_run_token("maintenance", "run-scale-token", ["runner.scale"], ttl_seconds=60.0)
    from identity import identity_manager

    prin = identity_manager.verify_token(raw)
    assert prin is not None
    assert principal_has_scope(prin, "runners.scale")
    for forbidden in ("fleet.control", "runners.control", "fleet.maintain"):
        assert not principal_has_scope(prin, forbidden), forbidden


def test_fleet_peer_preset_includes_runners_scale() -> None:
    from identity import FLEET_PEER_SCOPES

    assert "runners.scale" in FLEET_PEER_SCOPES


def test_target_audit_entry_visible_by_principal_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    import orchestration_audit as oa

    monkeypatch.setattr(oa, "ORCHESTRATION_AUDIT_PATH", tmp_path / "audit.ndjson")
    principal = _scale_principal()
    entry = routes._audit_entry(
        "aid",
        "completed",
        principal,
        {"host": "hub-a", "runners": 2, "until": None, "reason": "x"},
        auth_scope="runners.scale",
    )
    asyncio.run(oa.append_orchestration_audit(entry))
    rows = oa.load_orchestration_audit(limit=10, principal=principal.id)
    assert len(rows) == 1
    assert rows[0]["auth_scope"] == "runners.scale"


def test_scale_only_forbidden_on_fleet_control_routes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Scale-only principal must not invoke other fleet.control endpoints."""
    from routers import fleet_maintenance_routes as maint

    scheduler = tmp_path / "runner-scheduler"
    scheduler.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(sched, "_runner_scheduler_bin", str(scheduler))
    monkeypatch.setattr(sched, "_runner_schedule_config", tmp_path / "schedule.json")
    monkeypatch.setattr(sched, "_runner_scheduler_state", tmp_path / "state.json")
    monkeypatch.setattr(sched, "_runner_base_dir", tmp_path)
    app = FastAPI()
    app.include_router(maint.router)
    app.dependency_overrides[require_principal] = _scale_principal
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(FLEET_CONTROL_URL, json={"action": "drain"})
    assert response.status_code == 403