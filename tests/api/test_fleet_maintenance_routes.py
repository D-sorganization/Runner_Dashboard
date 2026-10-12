"""POST /api/fleet/maintenance: privileged drain/resume of this host's runners (#1936).

The endpoint can reboot the host, so these tests pin: scope enforcement, strict
validation with NOTHING executed on any 400, and the exact argv built.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from routers import fleet_maintenance_routes as maint
from routers import orchestration_schedule_routes as sched

OPERATOR = Principal(id="op", type="human", name="Op", roles=["operator"], scopes=["fleet.control"])
VIEWER = Principal(id="viewer", type="human", name="Viewer", roles=["viewer"])
URL = "/api/fleet/maintenance"


class FakeRun:
    """Stand-in for ``subprocess.run`` that records every call."""

    def __init__(self, returncode: int = 0, stderr: str = "", exc: Exception | None = None) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.returncode = returncode
        self.stderr = stderr
        self.exc = exc

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(argv, self.returncode, stdout="started", stderr=self.stderr)


@pytest.fixture
def scheduler_bin(tmp_path: Path) -> Path:
    binary = tmp_path / "runner-scheduler"
    binary.write_text("#!/bin/sh\n")
    return binary


@pytest.fixture
def audit(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    async def _append(entry: dict[str, Any]) -> None:
        entries.append(entry)

    monkeypatch.setattr(maint._audit, "append_orchestration_audit", _append)
    return entries


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    fake = FakeRun()
    monkeypatch.setattr(maint.subprocess, "run", fake)
    return fake


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, scheduler_bin: Path, tmp_path: Path, audit: list) -> TestClient:  # noqa: ARG001
    monkeypatch.setattr(sched, "_runner_scheduler_bin", str(scheduler_bin))
    monkeypatch.setattr(sched, "_runner_schedule_config", tmp_path / "schedule.json")
    monkeypatch.setattr(sched, "_runner_scheduler_state", tmp_path / "state.json")
    monkeypatch.setattr(sched, "_runner_base_dir", tmp_path / "runners")
    app = FastAPI()
    app.include_router(maint.router)
    app.dependency_overrides[require_scope("fleet.control")] = lambda: OPERATOR
    return TestClient(app, raise_server_exceptions=False)


def _env_args(tmp_path: Path) -> list[str]:
    return [
        f"--setenv=RUNNER_ROOT={tmp_path / 'runners'}",
        f"--setenv=RUNNER_SCHEDULE_CONFIG={tmp_path / 'schedule.json'}",
        f"--setenv=RUNNER_SCHEDULER_STATE={tmp_path / 'state.json'}",
    ]


# --- auth -------------------------------------------------------------------


@pytest.mark.unit
def test_unauthenticated_request_is_rejected_and_nothing_runs(
    scheduler_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: FakeRun
) -> None:
    monkeypatch.setattr(sched, "_runner_scheduler_bin", str(scheduler_bin))
    monkeypatch.setattr(sched, "_runner_schedule_config", tmp_path / "s.json")
    monkeypatch.setattr(sched, "_runner_scheduler_state", tmp_path / "st.json")
    monkeypatch.setattr(sched, "_runner_base_dir", tmp_path)
    monkeypatch.delenv("DASHBOARD_LOOPBACK_AUTH", raising=False)
    app = FastAPI()
    app.include_router(maint.router)
    res = TestClient(app, raise_server_exceptions=False).post(URL, json={"action": "drain"})
    assert res.status_code in (401, 403)
    assert run.calls == []


@pytest.mark.unit
def test_wrong_scope_is_forbidden_and_nothing_runs(
    scheduler_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: FakeRun
) -> None:
    monkeypatch.setattr(sched, "_runner_scheduler_bin", str(scheduler_bin))
    monkeypatch.setattr(sched, "_runner_schedule_config", tmp_path / "s.json")
    monkeypatch.setattr(sched, "_runner_scheduler_state", tmp_path / "st.json")
    monkeypatch.setattr(sched, "_runner_base_dir", tmp_path)
    app = FastAPI()
    app.include_router(maint.router)
    app.dependency_overrides[require_principal] = lambda: VIEWER
    res = TestClient(app, raise_server_exceptions=False).post(URL, json={"action": "drain", "reboot": True})
    assert res.status_code == 403
    assert run.calls == []


# --- validation: every 400 executes nothing -----------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [
        [],
        ["drain"],
        "drain",
        42,
        None,
        {},
        {"reboot": True},
        {"action": None},
        {"action": ""},
        {"action": "reboot"},
        {"action": "DRAIN"},
        {"action": " drain"},
        {"action": ["drain"]},
        {"action": {"drain": 1}},
        {"action": 1},
        {"action": "drain; reboot"},
        {"action": "drain", "reboot": "true"},
        {"action": "drain", "reboot": 1},
        {"action": "drain", "reboot": 0},
        {"action": "drain", "reboot": None},
        {"action": "drain", "reboot": [True]},
        {"action": "resume", "reboot": True},
        {"action": "drain", "extra": "--now"},
        {"action": "drain", "scheduler_bin": "/bin/sh"},
    ],
)
def test_invalid_body_is_400_and_executes_nothing(client: TestClient, run: FakeRun, audit: list, payload: Any) -> None:
    res = client.post(URL, json=payload)
    assert res.status_code == 400, res.text
    assert run.calls == []
    assert audit == []


@pytest.mark.unit
def test_malformed_json_is_400_and_executes_nothing(client: TestClient, run: FakeRun) -> None:
    res = client.post(URL, content=b"{not json", headers={"content-type": "application/json"})
    assert res.status_code == 400
    assert run.calls == []


@pytest.mark.unit
def test_empty_body_is_400_and_executes_nothing(client: TestClient, run: FakeRun) -> None:
    assert client.post(URL).status_code == 400
    assert run.calls == []


# --- exact argv ------------------------------------------------------------------


@pytest.mark.unit
def test_drain_argv_is_transient_unit_without_wait(
    client: TestClient, run: FakeRun, scheduler_bin: Path, tmp_path: Path
) -> None:
    res = client.post(URL, json={"action": "drain"})
    assert res.status_code == 200, res.text
    assert len(run.calls) == 1
    argv, kwargs = run.calls[0]
    assert argv == [
        "sudo",
        "-n",
        "systemd-run",
        "--unit=runner-maintenance-drain",
        "--collect",
        *_env_args(tmp_path),
        str(scheduler_bin),
        "--apply",
        "--drain",
    ]
    assert "--wait" not in argv
    assert kwargs.get("shell") in (None, False)
    assert res.json()["action"] == "drain"
    assert res.json()["reboot"] is False


@pytest.mark.unit
def test_drain_with_reboot_argv(client: TestClient, run: FakeRun, scheduler_bin: Path, tmp_path: Path) -> None:
    res = client.post(URL, json={"action": "drain", "reboot": True})
    assert res.status_code == 200, res.text
    argv = run.calls[0][0]
    assert argv == [
        "sudo",
        "-n",
        "systemd-run",
        "--unit=runner-maintenance-drain",
        "--collect",
        *_env_args(tmp_path),
        str(scheduler_bin),
        "--apply",
        "--drain",
        "--reboot",
    ]


@pytest.mark.unit
def test_explicit_reboot_false_is_plain_drain(client: TestClient, run: FakeRun) -> None:
    assert client.post(URL, json={"action": "drain", "reboot": False}).status_code == 200
    assert "--reboot" not in run.calls[0][0]


@pytest.mark.unit
def test_resume_argv_waits(client: TestClient, run: FakeRun, scheduler_bin: Path, tmp_path: Path) -> None:
    res = client.post(URL, json={"action": "resume"})
    assert res.status_code == 200, res.text
    argv = run.calls[0][0]
    assert argv == [
        "sudo",
        "-n",
        "systemd-run",
        "--wait",
        "--collect",
        *_env_args(tmp_path),
        str(scheduler_bin),
        "--apply",
        "--resume",
    ]


@pytest.mark.unit
def test_argv_is_all_strings_and_request_data_never_reaches_it(client: TestClient, run: FakeRun) -> None:
    client.post(URL, json={"action": "drain", "reboot": False})
    argv = run.calls[0][0]
    assert all(isinstance(a, str) for a in argv)
    assert len(argv) == 11


# --- failures ------------------------------------------------------------------


@pytest.mark.unit
def test_missing_scheduler_is_503_and_executes_nothing(client: TestClient, run: FakeRun, scheduler_bin: Path) -> None:
    scheduler_bin.unlink()
    res = client.post(URL, json={"action": "drain"})
    assert res.status_code == 503
    assert run.calls == []


@pytest.mark.unit
def test_command_failure_is_500_with_bounded_detail(client: TestClient, run: FakeRun) -> None:
    run.returncode = 1
    run.stderr = "Failed to start: unit exists " + "x" * 5000
    res = client.post(URL, json={"action": "resume"})
    assert res.status_code == 500
    assert "unit exists" in res.json()["detail"]
    assert len(res.json()["detail"]) < 1200


@pytest.mark.unit
@pytest.mark.parametrize("exc", [OSError("sudo missing"), subprocess.TimeoutExpired("sudo", 30)])
def test_launch_error_is_500(client: TestClient, run: FakeRun, exc: Exception) -> None:
    run.exc = exc
    assert client.post(URL, json={"action": "drain"}).status_code == 500


# --- audit -----------------------------------------------------------------------


@pytest.mark.unit
def test_audit_records_principal_before_and_after_execution(
    client: TestClient, run: FakeRun, audit: list[dict[str, Any]]
) -> None:
    client.post(URL, json={"action": "drain", "reboot": True})
    assert [e["phase"] for e in audit] == ["requested", "completed"]
    for entry in audit:
        assert entry["requested_by"] == "op"
        assert entry["action"] == "fleet.maintenance.drain"
        assert entry["reboot"] is True
    assert audit[-1]["outcome"] == "started"


@pytest.mark.unit
def test_audit_records_failure(client: TestClient, run: FakeRun, audit: list[dict[str, Any]]) -> None:
    run.returncode = 3
    client.post(URL, json={"action": "resume"})
    assert audit[-1]["phase"] == "completed"
    assert audit[-1]["outcome"] == "failed"
