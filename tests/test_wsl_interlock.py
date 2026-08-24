"""Unit tests for backend.wsl_interlock (Issue #1067)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from wsl_interlock import (
    check_wsl_teardown_safety,
    count_active_runner_workers,
    get_last_wsl_boot_time,
    get_wsl_lifecycle_diagnostics,
    record_wsl_teardown_audit,
)


def test_count_active_runner_workers_none_psutil() -> None:
    with patch("wsl_interlock.psutil", None):
        assert count_active_runner_workers() == 0


def test_count_active_runner_workers_with_mocked_procs() -> None:
    worker = MagicMock()
    worker.info = {"name": "Runner.Worker.exe", "cmdline": ["Runner.Worker.exe"]}
    unrelated = MagicMock()
    unrelated.info = {"name": "python", "cmdline": ["python", "main.py"]}
    dotnet_worker = MagicMock()
    dotnet_worker.info = {
        "name": "dotnet",
        "cmdline": ["dotnet", "/opt/actions-runner/bin/Runner.Worker.dll"],
    }

    with patch("wsl_interlock.psutil") as mock_psutil:
        mock_psutil.process_iter.return_value = [worker, unrelated, dotnet_worker]
        assert count_active_runner_workers() == 2


def test_check_wsl_teardown_safety_idle() -> None:
    decision = check_wsl_teardown_safety(active_worker_count=0)
    assert decision["allowed"] is True
    assert decision["active_workers"] == 0
    assert decision["emergency_override"] is False
    assert decision["decision_reason"] == "no_active_workers"


def test_check_wsl_teardown_safety_active_blocked() -> None:
    decision = check_wsl_teardown_safety(active_worker_count=3, emergency_override=False)
    assert decision["allowed"] is False
    assert decision["active_workers"] == 3
    assert "active_runner_workers_running" in decision["decision_reason"]


def test_check_wsl_teardown_safety_emergency_override() -> None:
    decision = check_wsl_teardown_safety(active_worker_count=3, emergency_override=True)
    assert decision["allowed"] is True
    assert decision["active_workers"] == 3
    assert "emergency_override_used" in decision["decision_reason"]


def test_record_wsl_teardown_audit(tmp_path: Path) -> None:
    entry = record_wsl_teardown_audit(
        initiator="test-watchdog",
        reason="test-reason",
        active_workers=2,
        allowed=False,
        override_used=False,
        log_dir=tmp_path,
        extra={"custom_field": "val"},
    )
    assert entry["initiator"] == "test-watchdog"
    assert entry["custom_field"] == "val"

    log_file = tmp_path / "wsl-teardown-audit.jsonl"
    assert log_file.is_file()
    loaded = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert loaded["initiator"] == "test-watchdog"
    assert loaded["reason"] == "test-reason"


def test_get_last_wsl_boot_time() -> None:
    with patch("wsl_interlock.psutil") as mock_psutil:
        mock_psutil.boot_time.return_value = 1_700_000_000.0
        boot = get_last_wsl_boot_time()
    assert boot is not None
    assert "2023" in boot or "202" in boot


def test_get_wsl_lifecycle_diagnostics(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "last_recovery_reason": "hang_detected",
                "interrupted_runner_count": 2,
            }
        ),
        encoding="utf-8",
    )
    with patch("wsl_interlock.count_active_runner_workers", return_value=1):
        diagnostics = get_wsl_lifecycle_diagnostics(state_file=state_file)
    assert diagnostics["last_recovery_reason"] == "hang_detected"
    assert diagnostics["interrupted_runner_count"] == 2
    assert diagnostics["active_worker_count"] == 1
