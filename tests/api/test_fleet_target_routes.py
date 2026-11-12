"""POST /api/fleet/target regression tests (#4308)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import runner_target_contract as rtc
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_fleet_peer, require_scope
from routers import fleet_target_routes as routes
from routers import orchestration_schedule_routes as sched

OPERATOR = Principal(id="op", type="human", name="Op", roles=["operator"], scopes=["fleet.control"])
URL = "/api/fleet/target"


class FakeRun:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='{"desired":2,"reason":"ok","busy":0}', stderr="")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
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

    def _op() -> Principal:
        return OPERATOR

    app.dependency_overrides[require_scope("fleet.control")] = _op
    app.dependency_overrides[require_fleet_peer] = lambda: None
    return TestClient(app)


def test_fleet_target_first_override_records_scheduled_before(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        routes,
        "_scheduled_desired_from_scheduler",
        lambda: 5,
    )
    response = client.post(
        URL,
        json={"host": "hub-a", "runners": 3, "apply": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["target_override"]["before"] == 5


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"host": "", "runners": 1},
        {"host": "hub-a", "runners": -1},
        {"host": "hub-a"},
        {"host": "hub-a", "runners": 1, "unknown_field": True},
        {"host": "hub-a", "runners": "x"},
    ],
)
def test_fleet_target_rejects_invalid_payload(client: TestClient, payload: dict[str, object]) -> None:
    response = client.post(URL, json=payload)
    assert response.status_code == 422, response.text


def test_fleet_target_set_request_validated_payload_contract() -> None:
    body = routes.FleetTargetSetRequest(host="  hub-a  ", runners=4, reason="", apply=False)
    payload = body.validated_payload()
    assert payload == {
        "host": "hub-a",
        "runners": 4,
        "until": None,
        "reason": "api: /api/fleet/target",
        "apply": False,
    }


def test_fleet_target_clamps_and_persists(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    response = client.post(
        URL,
        json={"host": "hub-a", "runners": 99, "until": "2099-01-01T00:00:00Z", "reason": "pytest"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runners"] == 6
    assert body["clamped_to"] == 6
    active = rtc.load_target_override()
    assert active is not None and active["runners"] == 6
