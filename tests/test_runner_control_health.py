"""Unit tests for backend/runner_control_health.py (module-coverage invariant, #3008)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from runner_control_health import (
    get_runner_control_health,
    record_runner_control_result,
    reset_runner_control_health,
)


@pytest.fixture(autouse=True)
def _clean_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("RUNNER_DASHBOARD_CONFIG_DIR", str(tmp_path))
    reset_runner_control_health()
    yield
    reset_runner_control_health()


def test_default_status_is_healthy_with_no_last_action() -> None:
    assert get_runner_control_health() == {"status": "healthy"}


def test_successful_action_keeps_healthy_status_and_drops_last_action() -> None:
    record_runner_control_result("stop", succeeded=False)
    record_runner_control_result("start", succeeded=True)
    assert get_runner_control_health() == {"status": "healthy"}


def test_failed_action_reports_degraded_status_with_last_action() -> None:
    record_runner_control_result("restart", succeeded=False)
    assert get_runner_control_health() == {"status": "degraded", "last_action": "restart"}


def test_unsupported_action_is_rejected() -> None:
    with pytest.raises(AssertionError):
        record_runner_control_result("delete", succeeded=False)


def test_status_persists_to_disk_and_survives_in_process_reset(tmp_path: Path) -> None:
    record_runner_control_result("stop", succeeded=False)
    status_file = tmp_path / "runner-control-health.json"
    assert json.loads(status_file.read_text(encoding="utf-8")) == {"status": "degraded", "last_action": "stop"}

    reset_runner_control_health()
    assert get_runner_control_health() == {"status": "healthy"}


def test_get_ignores_a_corrupt_status_file(tmp_path: Path) -> None:
    status_file = tmp_path / "runner-control-health.json"
    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text("not json", encoding="utf-8")
    assert get_runner_control_health() == {"status": "healthy"}
