"""POST /api/fleet/schedule: save path and clamp response (#4307, PR review)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope

_BACKEND = Path(__file__).resolve().parent.parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import server  # noqa: E402
from routers import orchestration_schedule_routes as sched  # noqa: E402

OPERATOR = Principal(id="op", type="human", name="Op", roles=["operator"], scopes=["fleet.control"])
URL = "/api/fleet/schedule"

_FLOOR = {
    "disk_path": "/mnt/c",
    "min_disk_free_gb": 40,
    "min_mem_available_gb": 6,
    "max_memory_pressure_avg60": 20,
    "runners": 1,
}


def _minimal_snapshot(schedule: dict[str, Any]) -> dict[str, Any]:
    return {
        "machine": "hub-a",
        "max_runners": schedule.get("max_count", 0),
        "schedule": schedule,
        "host_runner_hard_cap": 6,
    }


@pytest.fixture
def schedule_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "runner-schedule.json"
    monkeypatch.setattr(server, "RUNNER_SCHEDULE_CONFIG", path)
    monkeypatch.setattr(server, "HOSTNAME", "hub-a")
    monkeypatch.setattr(server, "RUNNER_ALIASES", [])
    monkeypatch.setattr(server, "_runner_limit", lambda: 16)

    def snapshot_after_save() -> dict[str, Any]:
        if path.is_file():
            live = json.loads(path.read_text(encoding="utf-8"))
        else:
            live = {"enabled": True, "max_count": 6}
        return _minimal_snapshot(live)

    sched.set_dependencies(
        get_runner_capacity_snapshot=snapshot_after_save,
        validate_runner_schedule=server._validate_runner_schedule,
        sanitize_runner_schedule=server._sanitize_runner_schedule,
        write_runner_schedule_config=server._write_runner_schedule_config,
        runner_scheduler_apply_command=server._runner_scheduler_apply_command,
        runner_scheduler_bin=str(tmp_path / "missing-scheduler"),
        runner_schedule_config=path,
        runner_scheduler_state=tmp_path / "state.json",
        runner_base_dir=tmp_path / "runners",
    )
    return path


@pytest.fixture
def client(schedule_path: Path) -> TestClient:  # noqa: ARG001
    app = FastAPI()
    app.include_router(sched.router)
    app.dependency_overrides[require_scope("fleet.control")] = lambda: OPERATOR
    return TestClient(app, raise_server_exceptions=True)


@pytest.mark.unit
def test_post_schedule_persists_resource_floor(client: TestClient, schedule_path: Path) -> None:
    body = {
        "schedule": {
            "enabled": True,
            "timezone": "America/Los_Angeles",
            "default_count": 1,
            "max_count": 4,
            "schedules": [],
            "resource_floor": _FLOOR,
        }
    }
    res = client.post(URL, json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["saved"] is True
    assert data["schedule"].get("resource_floor") == _FLOOR

    saved = json.loads(schedule_path.read_text(encoding="utf-8"))
    assert saved["resource_floor"] == _FLOOR


@pytest.mark.unit
def test_post_schedule_over_cap_returns_clamp_notices(client: TestClient, schedule_path: Path) -> None:
    res = client.post(
        URL,
        json={
            "schedule": {
                "enabled": True,
                "timezone": "America/Los_Angeles",
                "default_count": 1,
                "max_count": 16,
                "schedules": [
                    {
                        "name": "peak",
                        "days": ["mon"],
                        "start": "08:00",
                        "end": "17:00",
                        "runners": 16,
                    }
                ],
            }
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["capacity_clamped"] is True
    assert data["clamp_notices"]
    assert any("max_count clamped" in note for note in data["clamp_notices"])
    assert data["max_runners"] == 6

    saved = json.loads(schedule_path.read_text(encoding="utf-8"))
    assert saved["max_count"] == 6
    assert saved["schedules"][0]["runners"] == 6
