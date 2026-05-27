"""Tests for system_utils.py pure helper functions.

Covers get_workload_capacity_from_specs, get_disk_pressure_snapshot,
classify_node_offline, resource_offline_reason, and get_deployment_info.
These are all pure (or near-pure) functions with no external I/O.
"""

from __future__ import annotations

import errno
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

import system_utils  # noqa: E402


def _cp(stdout: str = "", returncode: int = 0) -> MagicMock:
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.stdout = stdout
    cp.returncode = returncode
    cp.stderr = ""
    return cp


# ---------------------------------------------------------------------------
# get_workload_capacity_from_specs
# ---------------------------------------------------------------------------


def test_workload_capacity_gpu_tag_added() -> None:
    specs = {"cpu_logical_cores": 8, "memory_gb": 16.0, "gpu_vram_gb": 8.0, "gpu_count": 1}
    result = system_utils.get_workload_capacity_from_specs(specs)
    assert "gpu" in result["tags"]
    assert result["gpu_slots"] == 1


def test_workload_capacity_parallel_ci_tag_added() -> None:
    specs = {"cpu_logical_cores": 16, "memory_gb": 32.0, "gpu_vram_gb": None, "gpu_count": 0}
    result = system_utils.get_workload_capacity_from_specs(specs)
    assert "parallel-ci" in result["tags"]


def test_workload_capacity_memory_heavy_tag_added() -> None:
    specs = {"cpu_logical_cores": 8, "memory_gb": 64.0, "gpu_vram_gb": None, "gpu_count": 0}
    result = system_utils.get_workload_capacity_from_specs(specs)
    assert "memory-heavy" in result["tags"]


def test_workload_capacity_small_ci_tag_added() -> None:
    specs = {"cpu_logical_cores": 2, "memory_gb": 4.0, "gpu_vram_gb": None, "gpu_count": 0}
    result = system_utils.get_workload_capacity_from_specs(specs)
    assert "small-ci" in result["tags"]


def test_workload_capacity_cpu_slots_correct() -> None:
    specs = {"cpu_logical_cores": 8, "memory_gb": 16.0, "gpu_vram_gb": None, "gpu_count": 0}
    result = system_utils.get_workload_capacity_from_specs(specs)
    assert result["cpu_slots"] == 4  # 8 // 2


def test_workload_capacity_memory_slots_correct() -> None:
    specs = {"cpu_logical_cores": 4, "memory_gb": 32.0, "gpu_vram_gb": None, "gpu_count": 0}
    result = system_utils.get_workload_capacity_from_specs(specs)
    assert result["memory_slots"] == 4  # 32 // 8


def test_workload_capacity_none_cores_returns_none_slots() -> None:
    specs = {"cpu_logical_cores": None, "memory_gb": None, "gpu_vram_gb": None, "gpu_count": 0}
    result = system_utils.get_workload_capacity_from_specs(specs)
    assert result["cpu_slots"] is None
    assert result["memory_slots"] is None


def test_workload_capacity_zero_cores_returns_none_slots() -> None:
    specs = {"cpu_logical_cores": 0, "memory_gb": 0, "gpu_vram_gb": None, "gpu_count": 0}
    result = system_utils.get_workload_capacity_from_specs(specs)
    assert result["cpu_slots"] is None
    assert result["memory_slots"] is None


def test_workload_capacity_tags_sorted() -> None:
    specs = {"cpu_logical_cores": 16, "memory_gb": 64.0, "gpu_vram_gb": 8.0, "gpu_count": 1}
    result = system_utils.get_workload_capacity_from_specs(specs)
    assert result["tags"] == sorted(result["tags"])


def test_workload_capacity_no_extra_tags_for_mid_range_hardware() -> None:
    # 6 cores, 16 GB RAM, no GPU — no special tags expected
    specs = {"cpu_logical_cores": 6, "memory_gb": 16.0, "gpu_vram_gb": None, "gpu_count": 0}
    result = system_utils.get_workload_capacity_from_specs(specs)
    assert result["tags"] == []


# ---------------------------------------------------------------------------
# get_disk_pressure_snapshot
# ---------------------------------------------------------------------------


def test_disk_pressure_healthy_state() -> None:
    result = system_utils.get_disk_pressure_snapshot(
        path="/data",
        total_gb=500.0,
        used_gb=200.0,
        free_gb=300.0,
        percent=40.0,
    )
    assert result["status"] == "healthy"
    assert result["reasons"] == []
    assert result["recommendations"] == []


def test_disk_pressure_warning_on_high_usage() -> None:
    result = system_utils.get_disk_pressure_snapshot(
        path="/data",
        total_gb=500.0,
        used_gb=430.0,
        free_gb=70.0,
        percent=86.0,  # > DISK_WARN_PERCENT (85)
    )
    assert result["status"] == "warning"
    assert any("85" in r or "warn" in r.lower() for r in result["reasons"])


def test_disk_pressure_critical_on_very_high_usage() -> None:
    result = system_utils.get_disk_pressure_snapshot(
        path="/data",
        total_gb=500.0,
        used_gb=470.0,
        free_gb=30.0,
        percent=94.0,  # > DISK_CRITICAL_PERCENT (92)
    )
    assert result["status"] == "critical"


def test_disk_pressure_warning_on_low_free_space() -> None:
    result = system_utils.get_disk_pressure_snapshot(
        path="/data",
        total_gb=2000.0,
        used_gb=1985.0,
        free_gb=15.0,  # <= DISK_MIN_FREE_GB (25)
        percent=50.0,
    )
    # Low free space should trigger at least a warning
    assert result["status"] in ("warning", "critical")
    assert any("free" in r.lower() or "GB" in r for r in result["reasons"])


def test_disk_pressure_has_recommendations_when_not_healthy() -> None:
    result = system_utils.get_disk_pressure_snapshot(
        path="/data",
        total_gb=100.0,
        used_gb=95.0,
        free_gb=5.0,
        percent=95.0,
    )
    assert result["status"] != "healthy"
    assert len(result["recommendations"]) > 0


def test_disk_pressure_snapshot_includes_thresholds() -> None:
    result = system_utils.get_disk_pressure_snapshot(
        path="/data",
        total_gb=500.0,
        used_gb=200.0,
        free_gb=300.0,
        percent=40.0,
    )
    assert "warn_percent" in result
    assert "critical_percent" in result
    assert "min_free_gb" in result


def test_disk_pressure_path_passthrough() -> None:
    result = system_utils.get_disk_pressure_snapshot(
        path="/runners",
        total_gb=500.0,
        used_gb=100.0,
        free_gb=400.0,
        percent=20.0,
    )
    assert result["path"] == "/runners"


def test_windows_host_resource_snapshot_uses_absolute_powershell_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        system_utils.platform,
        "uname",
        lambda: type("Uname", (), {"release": "6.6.87.2-microsoft-standard-WSL2"})(),
    )
    # Pin the candidate list so the test does not depend on the runner's
    # actual /mnt/* drives (e.g. ControlTower-SSD runners mount /mnt/d).
    monkeypatch.setattr(
        system_utils,
        "get_powershell_candidates",
        lambda: [
            "powershell.exe",
            "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        ],
    )
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args[0])
        if args[0] == "powershell.exe":
            raise OSError("PATH unavailable under systemd")
        return _cp(
            json.dumps(
                {
                    "cpu_percent": 17.0,
                    "memory_total_gb": 127.9,
                    "memory_used_gb": 52.0,
                    "memory_available_gb": 75.9,
                    "memory_percent": 40.7,
                }
            )
        )

    monkeypatch.setattr(system_utils.subprocess, "run", fake_run)

    assert system_utils._windows_host_resource_snapshot() == {
        "cpu_percent": 17.0,
        "memory_total_gb": 127.9,
        "memory_used_gb": 52.0,
        "memory_available_gb": 75.9,
        "memory_percent": 40.7,
    }
    assert calls == [
        "powershell.exe",
        "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    ]


# ---------------------------------------------------------------------------
# classify_node_offline
# ---------------------------------------------------------------------------


def test_classify_node_offline_timeout_exception() -> None:
    # Uses isinstance(exc, httpx.TimeoutException) — not string matching.
    exc = httpx.ConnectTimeout("timed out")
    result = system_utils.classify_node_offline(exc)
    assert result["offline_reason"] == "timeout"


def test_classify_node_offline_read_timeout() -> None:
    exc = httpx.ReadTimeout("read timed out")
    result = system_utils.classify_node_offline(exc)
    assert result["offline_reason"] == "timeout"


def test_classify_node_offline_connection_refused() -> None:
    # Uses isinstance(exc, httpx.ConnectError) + OSError.errno — not string matching.
    os_err = OSError()
    os_err.errno = errno.ECONNREFUSED
    exc = httpx.ConnectError("connection refused")
    exc.__cause__ = os_err
    result = system_utils.classify_node_offline(exc)
    assert result["offline_reason"] == "refused"


def test_classify_node_offline_no_route_to_host() -> None:
    os_err = OSError()
    os_err.errno = errno.ENETUNREACH
    exc = httpx.ConnectError("network unreachable")
    exc.__cause__ = os_err
    result = system_utils.classify_node_offline(exc)
    assert result["offline_reason"] == "network"


def test_classify_node_offline_host_unreachable() -> None:
    os_err = OSError()
    os_err.errno = errno.EHOSTUNREACH
    exc = httpx.ConnectError("host unreachable")
    exc.__cause__ = os_err
    result = system_utils.classify_node_offline(exc)
    assert result["offline_reason"] == "network"


def test_classify_node_offline_connect_error_no_os_cause() -> None:
    # ConnectError without an OS cause defaults to "refused".
    exc = httpx.ConnectError("connect failed")
    result = system_utils.classify_node_offline(exc)
    assert result["offline_reason"] == "refused"


def test_classify_node_offline_401_status_code() -> None:
    result = system_utils.classify_node_offline(status_code=401)
    assert result["offline_reason"] == "auth"
    assert "401" in result["offline_detail"]


def test_classify_node_offline_403_status_code() -> None:
    result = system_utils.classify_node_offline(status_code=403)
    assert result["offline_reason"] == "auth"


def test_classify_node_offline_500_status_code() -> None:
    result = system_utils.classify_node_offline(status_code=500)
    assert result["offline_reason"] == "error"


def test_classify_node_offline_unknown_status_code() -> None:
    result = system_utils.classify_node_offline(status_code=418)
    assert result["offline_reason"] == "other"


def test_classify_node_offline_no_args() -> None:
    result = system_utils.classify_node_offline()
    assert result["offline_reason"] == "unknown"


def test_classify_node_offline_generic_exception() -> None:
    exc = RuntimeError("something weird happened")
    result = system_utils.classify_node_offline(exc)
    assert result["offline_reason"] == "other"
    assert "something weird" in result["offline_detail"]


# ---------------------------------------------------------------------------
# resource_offline_reason
# ---------------------------------------------------------------------------


def test_resource_offline_reason_healthy_system() -> None:
    system = {
        "disk": {"pressure": {"status": "healthy"}},
        "memory": {"percent": 60},
    }
    assert system_utils.resource_offline_reason(system) is None


def test_resource_offline_reason_critical_disk() -> None:
    system = {
        "disk": {"pressure": {"status": "critical", "reasons": ["disk usage >= 92%"]}},
        "memory": {"percent": 50},
    }
    result = system_utils.resource_offline_reason(system)
    assert result is not None
    assert result["offline_reason"] == "disk-pressure"


def test_resource_offline_reason_high_memory() -> None:
    system = {
        "disk": {"pressure": {"status": "healthy"}},
        "memory": {"percent": 99},
    }
    result = system_utils.resource_offline_reason(system)
    assert result is not None
    assert result["offline_reason"] == "oom-pressure"


def test_resource_offline_reason_memory_exactly_98_pct() -> None:
    system = {
        "disk": {"pressure": {"status": "healthy"}},
        "memory": {"percent": 98},
    }
    result = system_utils.resource_offline_reason(system)
    assert result is not None
    assert result["offline_reason"] == "oom-pressure"


def test_resource_offline_reason_memory_97_pct_is_none() -> None:
    system = {
        "disk": {"pressure": {"status": "healthy"}},
        "memory": {"percent": 97},
    }
    assert system_utils.resource_offline_reason(system) is None


def test_resource_offline_reason_empty_system_is_none() -> None:
    assert system_utils.resource_offline_reason({}) is None


# ---------------------------------------------------------------------------
# get_deployment_info
# ---------------------------------------------------------------------------


def test_get_deployment_info_from_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DASHBOARD_GIT_SHA", "abc123")
    monkeypatch.setenv("DASHBOARD_GIT_BRANCH", "main")
    result = system_utils.get_deployment_info("1.0.0", tmp_path / "nonexistent.json")
    assert result["git_sha"] == "abc123"
    assert result["git_branch"] == "main"
    assert result["version"] == "1.0.0"
    assert result["source"] == "environment"


def test_get_deployment_info_from_file(tmp_path: Path) -> None:
    deploy_file = tmp_path / "deployment.json"
    deploy_file.write_text(
        json.dumps({"git_sha": "deadbeef", "git_branch": "feature/x"}),
        encoding="utf-8",
    )
    result = system_utils.get_deployment_info("1.0.0", deploy_file)
    assert result["git_sha"] == "deadbeef"
    assert result["app"] == "runner-dashboard"
    assert result["source"] == "deployment-file"


def test_get_deployment_info_malformed_file_falls_back(tmp_path: Path) -> None:
    bad_file = tmp_path / "deployment.json"
    bad_file.write_text("not json", encoding="utf-8")
    result = system_utils.get_deployment_info("2.0.0", bad_file)
    assert result["version"] == "2.0.0"
    assert result["source"] == "environment"


def test_get_deployment_info_non_dict_file_falls_back(tmp_path: Path) -> None:
    bad_file = tmp_path / "deployment.json"
    bad_file.write_text("[1, 2, 3]", encoding="utf-8")
    result = system_utils.get_deployment_info("2.0.0", bad_file)
    assert result["source"] == "environment"


# ---------------------------------------------------------------------------
# get_wsl_host_disk_path
# ---------------------------------------------------------------------------


def test_get_wsl_host_disk_path() -> None:
    assert system_utils.get_wsl_host_disk_path("D:\\WSL\\distro") == "/mnt/d"
    assert system_utils.get_wsl_host_disk_path("d:\\WSL\\distro") == "/mnt/d"
    assert system_utils.get_wsl_host_disk_path("C:\\WSL\\distro") == "/mnt/c"
    assert system_utils.get_wsl_host_disk_path("c:\\WSL\\distro") == "/mnt/c"
    assert system_utils.get_wsl_host_disk_path(None) == "/mnt/c"
    assert system_utils.get_wsl_host_disk_path("xyz") == "/mnt/c"


# ---------------------------------------------------------------------------
# get_overall_disk_pressure
# ---------------------------------------------------------------------------


def test_get_overall_disk_pressure_empty() -> None:
    result = system_utils.get_overall_disk_pressure([])
    assert result["status"] == "healthy"


def test_get_overall_disk_pressure_single_healthy() -> None:
    pools = [
        {
            "percent": 50.0,
            "pressure": {"status": "healthy", "reasons": []},
        }
    ]
    result = system_utils.get_overall_disk_pressure(pools)
    assert result["status"] == "healthy"


def test_get_overall_disk_pressure_warning_vs_healthy() -> None:
    pools = [
        {
            "percent": 50.0,
            "pressure": {"status": "healthy", "reasons": []},
        },
        {
            "percent": 86.0,
            "pressure": {"status": "warning", "reasons": ["warning threshold exceeded"]},
        },
    ]
    result = system_utils.get_overall_disk_pressure(pools)
    assert result["status"] == "warning"
    assert "warning threshold exceeded" in result["reasons"]


def test_get_overall_disk_pressure_warning_vs_critical() -> None:
    pools = [
        {
            "percent": 86.0,
            "pressure": {"status": "warning", "reasons": ["warning threshold exceeded"]},
        },
        {
            "percent": 95.0,
            "pressure": {"status": "critical", "reasons": ["critical threshold exceeded"]},
        },
    ]
    result = system_utils.get_overall_disk_pressure(pools)
    assert result["status"] == "critical"
    assert "critical threshold exceeded" in result["reasons"]


def test_get_overall_disk_pressure_highest_percent_on_equal_status() -> None:
    pools = [
        {
            "percent": 86.0,
            "pressure": {"status": "warning", "reasons": ["warning threshold exceeded 1"]},
        },
        {
            "percent": 88.0,
            "pressure": {"status": "warning", "reasons": ["warning threshold exceeded 2"]},
        },
    ]
    result = system_utils.get_overall_disk_pressure(pools)
    assert result["status"] == "warning"
    assert "warning threshold exceeded 2" in result["reasons"]


# ---------------------------------------------------------------------------
# get_io_pressure_snapshot
# ---------------------------------------------------------------------------


def test_get_io_pressure_snapshot_not_exists(tmp_path: Path, monkeypatch) -> None:
    # Point the Path to a non-existent file
    monkeypatch.setattr(system_utils, "Path", lambda *args, **kwargs: tmp_path / "nonexistent")
    assert system_utils.get_io_pressure_snapshot() is None


def test_get_io_pressure_snapshot_valid(tmp_path: Path, monkeypatch) -> None:
    pressure_file = tmp_path / "io"
    pressure_file.write_text(
        "some avg10=0.15 avg60=0.08 avg300=0.02 total=123456\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(system_utils, "Path", lambda *args, **kwargs: pressure_file)
    result = system_utils.get_io_pressure_snapshot()
    assert result == {
        "some": {
            "avg10": 0.15,
            "avg60": 0.08,
            "avg300": 0.02,
            "total": 123456,
        },
        "full": {
            "avg10": 0.0,
            "avg60": 0.0,
            "avg300": 0.0,
            "total": 0,
        },
    }


def test_get_io_pressure_snapshot_malformed(tmp_path: Path, monkeypatch) -> None:
    pressure_file = tmp_path / "io"
    pressure_file.write_text("invalid content", encoding="utf-8")
    monkeypatch.setattr(system_utils, "Path", lambda *args, **kwargs: pressure_file)
    # Malformed lines shouldn't cause a crash, just return None or partial.
    # In our parser, if no keys match, it returns None or empty dict.
    assert system_utils.get_io_pressure_snapshot() is None
