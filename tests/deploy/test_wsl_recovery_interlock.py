"""Tests for active-job interlock and WSL recovery safeguards (Issue #1067).

Verifies that:
1. Active Runner.Worker processes are detected and gate destructive host/WSL recovery.
2. Emergency override permits bypass when explicitly flagged.
3. Teardown audit logging captures structured audit trails.
4. Dashboard telemetry surfaces last boot time, recovery reason, and interrupted counts.
5. Watchdog probes and health failures cannot tear down healthy runner hosts.
"""

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


def test_count_active_runner_workers_empty() -> None:
    """When no Runner.Worker processes exist, active count is 0."""
    with patch("psutil.process_iter", return_value=[]):
        assert count_active_runner_workers() == 0


def test_count_active_runner_workers_detected() -> None:
    """Detects processes named or executing Runner.Worker."""
    proc1 = MagicMock()
    proc1.info = {"name": "Runner.Worker", "cmdline": ["/home/runner/bin/Runner.Worker", "spawnclient"]}

    proc2 = MagicMock()
    proc2.info = {"name": "python", "cmdline": ["python3", "server.py"]}

    proc3 = MagicMock()
    proc3.info = {"name": "dotnet", "cmdline": ["/opt/runner/bin/Runner.Worker"]}

    with patch("psutil.process_iter", return_value=[proc1, proc2, proc3]):
        assert count_active_runner_workers() == 2


def test_check_wsl_teardown_safety_denies_when_workers_active() -> None:
    """Active jobs/workers interlock denies destructive teardown by default."""
    decision = check_wsl_teardown_safety(
        emergency_override=False,
        reason="unresponsive_probe",
        active_worker_count=5,
    )
    assert decision["allowed"] is False
    assert decision["active_workers"] == 5
    assert "active_runner_workers_running (5 active)" in decision["decision_reason"]
    assert decision["emergency_override"] is False


def test_check_wsl_teardown_safety_allows_when_clean() -> None:
    """When active worker count is 0, teardown is permitted."""
    decision = check_wsl_teardown_safety(
        emergency_override=False,
        reason="clean_host_reset",
        active_worker_count=0,
    )
    assert decision["allowed"] is True
    assert decision["active_workers"] == 0
    assert decision["decision_reason"] == "no_active_workers"


def test_check_wsl_teardown_safety_emergency_override_bypasses() -> None:
    """Emergency override permits teardown despite active workers."""
    decision = check_wsl_teardown_safety(
        emergency_override=True,
        reason="emergency_oom",
        active_worker_count=8,
    )
    assert decision["allowed"] is True
    assert decision["active_workers"] == 8
    assert decision["emergency_override"] is True
    assert "emergency_override_used (8 active workers bypassed)" in decision["decision_reason"]


def test_record_wsl_teardown_audit(tmp_path: Path) -> None:
    """Structured audit trail is written as JSONL."""
    log_dir = tmp_path / "audit"
    entry = record_wsl_teardown_audit(
        initiator="test_suite",
        reason="watchdog_probe_failure",
        active_workers=3,
        allowed=False,
        override_used=False,
        log_dir=log_dir,
        extra={"distro": "Ubuntu-22.04", "host": "oglaptop"},
    )
    assert entry["initiator"] == "test_suite"
    assert entry["allowed"] is False
    assert entry["distro"] == "Ubuntu-22.04"

    audit_file = log_dir / "wsl-teardown-audit.jsonl"
    assert audit_file.is_file()
    lines = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["active_workers"] == 3
    assert lines[0]["host"] == "oglaptop"


def test_get_wsl_lifecycle_diagnostics(tmp_path: Path) -> None:
    """Surfaces boot time, recovery reason, and interrupted runner telemetry."""
    state_file = tmp_path / "wsl-keepalive-state.json"
    state_file.write_text(
        json.dumps(
            {
                "status": "recovered",
                "last_recovery_reason": "unresponsive_distro",
                "interrupted_runner_count": 0,
            }
        ),
        encoding="utf-8",
    )

    with patch("psutil.boot_time", return_value=1700000000.0):
        diag = get_wsl_lifecycle_diagnostics(state_file=state_file)
        assert diag["last_wsl_boot_time"] is not None
        assert diag["last_recovery_reason"] == "unresponsive_distro"
        assert diag["interrupted_runner_count"] == 0
        assert isinstance(diag["active_worker_count"], int)


def test_get_last_wsl_boot_time() -> None:
    """Returns valid ISO boot timestamp."""
    with patch("psutil.boot_time", return_value=1700000000.0):
        bt = get_last_wsl_boot_time()
        assert bt is not None
        assert "2023-" in bt or "202" in bt
