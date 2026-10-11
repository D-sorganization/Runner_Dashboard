"""POST /api/fleet/maintenance: dashboard drain/maintenance action (#1929 item 3)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from identity import Principal, require_principal  # noqa: E402
from routers import orchestration_schedule_routes as routes  # noqa: E402


@pytest.fixture
def harness(tmp_path: Path, monkeypatch):
    scheduler = tmp_path / "runner-scheduler"
    scheduler.write_text("#!/bin/sh\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(routes.subprocess, "run", fake_run)
    routes.set_dependencies(
        get_runner_capacity_snapshot=lambda: {"state": {"maintenance": {"active": True, "drained": False}}},
        validate_runner_schedule=lambda c: c,
        write_runner_schedule_config=lambda c: None,
        runner_scheduler_apply_command=lambda: ["true"],
        runner_scheduler_bin=str(scheduler),
        runner_schedule_config=tmp_path / "schedule.json",
        runner_scheduler_state=tmp_path / "state.json",
        runner_base_dir=tmp_path,
    )
    app = FastAPI()
    app.include_router(routes.router)
    scopes = ["fleet.control"]
    app.dependency_overrides[require_principal] = lambda: Principal(
        id="op", type="human", name="Op", roles=["admin"], scopes=scopes
    )
    return TestClient(app, raise_server_exceptions=False), calls, scheduler


def test_drain_launches_detached_scheduler_drain(harness) -> None:
    client, calls, scheduler = harness

    resp = client.post("/api/fleet/maintenance", json={"action": "drain"})

    assert resp.status_code == 200, resp.text
    cmd = calls[0]
    assert cmd[:3] == ["sudo", "-n", "systemd-run"]  # non-blocking: the drain can take an hour
    assert str(scheduler) in cmd and "--apply" in cmd and "--drain" in cmd
    assert "--reboot" not in cmd
    assert resp.json()["state"]["maintenance"]["active"] is True


def test_drain_with_reboot_passes_reboot_flag(harness) -> None:
    client, calls, _ = harness

    assert client.post("/api/fleet/maintenance", json={"action": "drain", "reboot": True}).status_code == 200

    assert "--reboot" in calls[0]


def test_resume_clears_the_hold(harness) -> None:
    client, calls, scheduler = harness

    assert client.post("/api/fleet/maintenance", json={"action": "resume"}).status_code == 200

    assert calls[0][:2] == ["sudo", "-n"] and "--resume" in calls[0] and "--drain" not in calls[0]


@pytest.mark.parametrize(
    "body",
    [{}, {"action": "explode"}, {"action": "resume", "reboot": True}, {"action": "drain", "reboot": "yes"}, []],
)
def test_invalid_requests_are_rejected_without_running_anything(harness, body) -> None:
    client, calls, _ = harness

    assert client.post("/api/fleet/maintenance", json=body).status_code == 400
    assert calls == []


def test_missing_scheduler_binary_is_a_503(harness) -> None:
    client, calls, scheduler = harness
    scheduler.unlink()

    assert client.post("/api/fleet/maintenance", json={"action": "drain"}).status_code == 503
    assert calls == []


def test_route_is_hidden_from_the_openapi_snapshot() -> None:
    """Keeps the vendored frontend OpenAPI snapshot untouched (migration lock, #1800)."""
    app = FastAPI()
    app.include_router(routes.router)
    assert "/api/fleet/maintenance" not in app.openapi()["paths"]
