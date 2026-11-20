"""Scheduler wiring, host flag, dry-run default and the status route (Refs #4249)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from staff.review_arm import runtime


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    for key in list(__import__("os").environ):
        if key.startswith("RD_REVIEW_ARM_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("RD_REVIEW_ARM_LEDGER", str(tmp_path / "ledger.sqlite3"))
    cycles: list[object] = []
    monkeypatch.setattr(runtime, "_loop_body", lambda service, interval: cycles.append(service))
    yield
    runtime.stop_review_arm()
    runtime._service = None
    runtime._settings = None


def test_default_off_publishes_disabled_status_and_no_thread() -> None:
    assert runtime.start_review_arm(None, machine="hub-a") is False
    status = runtime.status()
    assert status["enabled"] is False
    assert status["started"] is False
    assert status["dry_run"] is True


def test_other_host_does_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RD_REVIEW_ARM_HOST", "hub-a")
    assert runtime.start_review_arm(None, machine="worker-b") is False
    assert runtime.status()["enabled"] is False


def test_matching_host_starts_once_in_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    monkeypatch.setenv("RD_REVIEW_ARM_HOST", "hub-a")
    loop = asyncio.new_event_loop()
    try:
        started = runtime.start_review_arm(loop, machine="HUB-A")
        assert started is True
        status = runtime.status()
        assert (status["enabled"], status["dry_run"], status["host"]) == (True, True, "hub-a")
        assert status["interval_seconds"] == 600
    finally:
        loop.close()


def test_invalid_config_disables_visibly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RD_REVIEW_ARM_HOST", "hub-a")
    monkeypatch.setenv("RD_REVIEW_ARM_STALL_MINUTES", "0")
    assert runtime.start_review_arm(None, machine="hub-a") is False
    assert runtime.status()["enabled"] is False


def test_box_state_import_reads_the_three_files(tmp_path: Path) -> None:
    from staff.review_arm.ledger import Ledger

    box = tmp_path / "box"
    box.mkdir()
    (box / "arm_holds.txt").write_text("Tools 5 # hold\n")
    (box / "blocked_heads.txt").write_text("Tools 6 abcdef12 seed\n")
    ledger = Ledger(tmp_path / "l.sqlite3")
    assert runtime.import_box_state(ledger, box) == {"holds": 1, "blocked": 1, "overrides": 0}
    assert runtime.import_box_state(ledger, box) == {"skipped": 1}


def test_status_route_requires_staff_read_and_returns_status() -> None:
    from identity import Principal, require_scope
    from routers import staff_review_arm

    app = FastAPI()
    app.include_router(staff_review_arm.router)
    reader = Principal(id="reader", type="human", name="Reader", scopes=["staff.read"])
    app.dependency_overrides[require_scope("staff.read")] = lambda: reader
    with TestClient(app) as client:
        body = client.get("/api/staff/review-arm/status").json()
    assert body["enabled"] is False
    assert "dry_run" in body


def test_status_route_is_registered_on_the_dashboard_app() -> None:
    source = (Path(__file__).resolve().parents[1] / "backend" / "server.py").read_text()
    assert "app.include_router(_staff_review_arm_router.router)" in source


def test_scheduler_bootstrap_starts_review_arm() -> None:
    import inspect

    from routers import staff_schedule

    assert "start_review_arm(loop)" in inspect.getsource(staff_schedule.start_scheduler)
