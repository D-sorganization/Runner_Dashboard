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
import time
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
    monkeypatch.setattr(system_utils, "_host_snapshot_cache", None)

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
# Persistent hardware-facts cache (cold-start /api/fleet/status 504 fix)
# ---------------------------------------------------------------------------


def _reset_hw_caches(monkeypatch, tmp_path: Path) -> Path:
    """Point the persistent facts file at tmp_path and clear in-memory caches."""
    facts_file = tmp_path / "hardware_facts.json"
    monkeypatch.setattr(system_utils, "_HARDWARE_FACTS_DIR", tmp_path)
    monkeypatch.setattr(system_utils, "_HARDWARE_FACTS_FILE", facts_file)
    system_utils._PHYSICAL_DISK_CACHE.clear()
    monkeypatch.setattr(system_utils, "_WSL_BASE_PATH_CACHE", None)
    monkeypatch.setattr(system_utils, "_WSL_BASE_PATH_LOOKED_UP", False)
    return facts_file


def test_physical_disk_props_persisted_after_first_probe(monkeypatch, tmp_path: Path) -> None:
    facts_file = _reset_hw_caches(monkeypatch, tmp_path)
    probe_calls = []

    def fake_probe(letter: str) -> dict[str, str]:
        probe_calls.append(letter)
        return {"media_type": "SSD", "bus_type": "NVMe"}

    monkeypatch.setattr(system_utils, "get_windows_drive_physical_properties", fake_probe)

    result = system_utils.get_cached_windows_drive_physical_properties("C")
    assert result == {"media_type": "SSD", "bus_type": "NVMe"}
    assert probe_calls == ["C"]
    # The static fact must be persisted with a timestamp for cross-restart reuse.
    persisted = json.loads(facts_file.read_text(encoding="utf-8"))
    assert persisted["physical_disks"]["C"]["media_type"] == "SSD"
    assert "_fetched_at" in persisted["physical_disks"]["C"]


def test_physical_disk_props_served_from_disk_after_restart(monkeypatch, tmp_path: Path) -> None:
    facts_file = _reset_hw_caches(monkeypatch, tmp_path)
    # Simulate a prior process having persisted the fact.
    facts_file.write_text(
        json.dumps(
            {
                "physical_disks": {
                    "C": {"media_type": "SSD", "bus_type": "NVMe", "_fetched_at": time.time()},
                }
            }
        ),
        encoding="utf-8",
    )

    def boom(letter: str) -> dict[str, str]:
        raise AssertionError("cold probe must NOT run when a fresh persisted fact exists")

    monkeypatch.setattr(system_utils, "get_windows_drive_physical_properties", boom)
    result = system_utils.get_cached_windows_drive_physical_properties("C")
    assert result == {"media_type": "SSD", "bus_type": "NVMe"}


def test_physical_disk_props_reprobe_after_ttl_expiry(monkeypatch, tmp_path: Path) -> None:
    facts_file = _reset_hw_caches(monkeypatch, tmp_path)
    facts_file.write_text(
        json.dumps(
            {
                "physical_disks": {
                    "C": {
                        "media_type": "Unknown",
                        "bus_type": "Unknown",
                        "_fetched_at": time.time() - system_utils._HW_FACTS_TTL_S - 1,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    probe_calls = []

    def fake_probe(letter: str) -> dict[str, str]:
        probe_calls.append(letter)
        return {"media_type": "HDD", "bus_type": "SATA"}

    monkeypatch.setattr(system_utils, "get_windows_drive_physical_properties", fake_probe)
    result = system_utils.get_cached_windows_drive_physical_properties("C")
    assert result == {"media_type": "HDD", "bus_type": "SATA"}
    assert probe_calls == ["C"]


def test_unknown_probe_result_is_persisted_to_avoid_cold_retax(monkeypatch, tmp_path: Path) -> None:
    """On hosts where Get-PhysicalDisk is slow AND returns nothing, persist the
    Unknown so restarts stay fast instead of re-paying the ~10s probe."""
    facts_file = _reset_hw_caches(monkeypatch, tmp_path)
    monkeypatch.setattr(
        system_utils,
        "get_windows_drive_physical_properties",
        lambda letter: {"media_type": "Unknown", "bus_type": "Unknown"},
    )
    result = system_utils.get_cached_windows_drive_physical_properties("C")
    assert result == {"media_type": "Unknown", "bus_type": "Unknown"}
    persisted = json.loads(facts_file.read_text(encoding="utf-8"))
    assert persisted["physical_disks"]["C"]["media_type"] == "Unknown"
    assert "_fetched_at" in persisted["physical_disks"]["C"]


def test_wsl_base_path_served_from_disk_after_restart(monkeypatch, tmp_path: Path) -> None:
    facts_file = _reset_hw_caches(monkeypatch, tmp_path)
    facts_file.write_text(json.dumps({"wsl_base_path": "C:\\WSL"}), encoding="utf-8")

    def boom() -> dict:
        raise AssertionError("registry probe must NOT run when base path is persisted")

    monkeypatch.setattr(system_utils, "get_wsl_distro_registry_info", boom)
    assert system_utils.get_cached_wsl_base_path() == "C:\\WSL"


def test_wsl_base_path_probed_and_persisted_on_cold_cache(monkeypatch, tmp_path: Path) -> None:
    facts_file = _reset_hw_caches(monkeypatch, tmp_path)
    monkeypatch.setattr(
        system_utils,
        "get_wsl_distro_registry_info",
        lambda: {"BasePath": "D:\\WSL\\ControlTower"},
    )
    assert system_utils.get_cached_wsl_base_path() == "D:\\WSL\\ControlTower"
    persisted = json.loads(facts_file.read_text(encoding="utf-8"))
    assert persisted["wsl_base_path"] == "D:\\WSL\\ControlTower"


def test_run_windows_powershell_respects_deadline(monkeypatch) -> None:
    """A fully-failing probe must stop iterating candidates once the wall-clock
    deadline is exhausted rather than spending 5s per candidate."""
    monkeypatch.setattr(
        system_utils.platform,
        "uname",
        lambda: type("Uname", (), {"release": "6.6-microsoft-standard-WSL2"})(),
    )
    monkeypatch.setattr(
        system_utils,
        "get_powershell_candidates",
        lambda: ["a.exe", "b.exe", "c.exe", "d.exe"],
    )
    seen_timeouts = []

    def fake_run(args, **kwargs):
        seen_timeouts.append(kwargs.get("timeout"))
        # Simulate the call consuming its whole timeout budget then failing.
        raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(system_utils.subprocess, "run", fake_run)
    # Deadline already in the past => no candidate should be attempted.
    assert system_utils._run_windows_powershell("x", deadline=time.monotonic() - 1) is None
    assert seen_timeouts == []
    # Per-candidate timeout must be clamped to <= 5s.
    seen_timeouts.clear()
    system_utils._run_windows_powershell("x", deadline=time.monotonic() + 5)
    assert all(t is not None and t <= 5.0 for t in seen_timeouts)


def test_run_windows_powershell_launches_hidden(monkeypatch) -> None:
    """Host powershell.exe probes must run with a hidden window so the WSL->Windows
    spec sync does not pop visible console windows on the operator's desktop."""
    monkeypatch.setattr(
        system_utils.platform,
        "uname",
        lambda: type("Uname", (), {"release": "6.6-microsoft-standard-WSL2"})(),
    )
    monkeypatch.setattr(system_utils, "get_powershell_candidates", lambda: ["ps.exe"])
    seen_args: list[list[str]] = []

    def fake_run(args, **kwargs):  # noqa: ARG001
        seen_args.append(list(args))
        return _cp(stdout="ok", returncode=0)

    monkeypatch.setattr(system_utils.subprocess, "run", fake_run)
    assert system_utils._run_windows_powershell("Get-Thing") == "ok"
    assert seen_args, "powershell candidate should have been invoked"
    args = seen_args[0]
    # The window must be suppressed; -NonInteractive avoids any prompt hang.
    assert "-WindowStyle" in args
    assert args[args.index("-WindowStyle") + 1] == "Hidden"
    assert "-NonInteractive" in args
    # The actual command must still be passed through.
    assert "-Command" in args and args[args.index("-Command") + 1] == "Get-Thing"


def test_host_snapshot_caches_within_ttl(monkeypatch) -> None:
    """Rapid successive calls must reuse one PowerShell fork within the TTL."""
    monkeypatch.setattr(system_utils, "_host_snapshot_cache", None)
    monkeypatch.setattr(system_utils, "_HOST_SNAPSHOT_TTL_S", 60.0)
    calls = []

    def fake_uncached() -> dict[str, float]:
        calls.append(1)
        return {
            "cpu_percent": 12.0,
            "memory_total_gb": 64.0,
            "memory_used_gb": 20.0,
            "memory_available_gb": 44.0,
            "memory_percent": 31.0,
        }

    monkeypatch.setattr(system_utils, "_windows_host_resource_snapshot_uncached", fake_uncached)
    first = system_utils._windows_host_resource_snapshot()
    second = system_utils._windows_host_resource_snapshot()
    assert first == second
    assert first is not None and first["cpu_percent"] == 12.0
    assert len(calls) == 1  # second call served from cache


def test_host_snapshot_serves_stale_on_probe_failure(monkeypatch) -> None:
    """If a refresh probe fails, the last good value is served, not None."""
    monkeypatch.setattr(
        system_utils,
        "_host_snapshot_cache",
        (
            {
                "cpu_percent": 5.0,
                "memory_total_gb": 1.0,
                "memory_used_gb": 0.5,
                "memory_available_gb": 0.5,
                "memory_percent": 50.0,
            },
            time.monotonic() - 999,
        ),
    )
    monkeypatch.setattr(system_utils, "_HOST_SNAPSHOT_TTL_S", 2.5)
    monkeypatch.setattr(system_utils, "_windows_host_resource_snapshot_uncached", lambda: None)
    result = system_utils._windows_host_resource_snapshot()
    assert result is not None
    assert result["cpu_percent"] == 5.0


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


# ---------------------------------------------------------------------------
# classify_disk_pressure_by_tier  (issue #754)
# ---------------------------------------------------------------------------


def test_tier_nvme_healthy_at_low_usage() -> None:
    """NVMe tier: 60% usage is healthy."""
    result = system_utils.classify_disk_pressure_by_tier(
        storage_tier="nvme",
        percent=60.0,
        free_gb=80.0,
        io_pressure_full_avg10=2.0,
    )
    assert result["tier"] == "nvme"
    assert result["status"] == "low"
    assert result["binding_constraint"] == "none"


def test_tier_nvme_io_saturation_triggers_high() -> None:
    """NVMe tier: IO saturation (full avg10 >= 50) escalates to high even at low capacity usage."""
    result = system_utils.classify_disk_pressure_by_tier(
        storage_tier="nvme",
        percent=40.0,
        free_gb=200.0,
        io_pressure_full_avg10=55.0,
    )
    assert result["tier"] == "nvme"
    assert result["status"] in ("high", "critical")
    assert result["binding_constraint"] == "io"


def test_tier_nvme_medium_io_pressure() -> None:
    """NVMe tier: moderate IO pressure (full avg10 20-49) triggers medium."""
    result = system_utils.classify_disk_pressure_by_tier(
        storage_tier="nvme",
        percent=40.0,
        free_gb=200.0,
        io_pressure_full_avg10=30.0,
    )
    assert result["tier"] == "nvme"
    assert result["status"] == "medium"
    assert result["binding_constraint"] == "io"


def test_tier_nvme_capacity_pressure_at_high_usage() -> None:
    """NVMe tier: capacity > 90% triggers at least medium even with healthy IO."""
    result = system_utils.classify_disk_pressure_by_tier(
        storage_tier="nvme",
        percent=92.0,
        free_gb=10.0,
        io_pressure_full_avg10=0.0,
    )
    assert result["tier"] == "nvme"
    assert result["status"] in ("medium", "high", "critical")
    assert result["binding_constraint"] == "capacity"


def test_tier_nvme_critical_io_and_capacity() -> None:
    """NVMe tier: both IO saturated and capacity critical => critical."""
    result = system_utils.classify_disk_pressure_by_tier(
        storage_tier="nvme",
        percent=95.0,
        free_gb=5.0,
        io_pressure_full_avg10=70.0,
    )
    assert result["tier"] == "nvme"
    assert result["status"] == "critical"


def test_tier_hdd_healthy_at_low_usage() -> None:
    """HDD tier: 50% usage with low IO is healthy."""
    result = system_utils.classify_disk_pressure_by_tier(
        storage_tier="hdd",
        percent=50.0,
        free_gb=200.0,
        io_pressure_full_avg10=5.0,
    )
    assert result["tier"] == "hdd"
    assert result["status"] == "low"
    assert result["binding_constraint"] == "none"


def test_tier_hdd_capacity_pressure_primary_signal() -> None:
    """HDD tier: capacity > 85% triggers medium (capacity is primary signal for HDD)."""
    result = system_utils.classify_disk_pressure_by_tier(
        storage_tier="hdd",
        percent=87.0,
        free_gb=50.0,
        io_pressure_full_avg10=5.0,
    )
    assert result["tier"] == "hdd"
    assert result["status"] in ("medium", "high", "critical")
    assert result["binding_constraint"] == "capacity"


# ---------------------------------------------------------------------------
# Issue #939d: run_cmd timeout must kill THEN reap, tolerating an already-exited
# process (no zombie/transport leak, no unhandled ProcessLookupError).
# ---------------------------------------------------------------------------


def test_run_cmd_timeout_kills_and_reaps(monkeypatch) -> None:
    import asyncio

    class _FakeProc:
        def __init__(self) -> None:
            self.killed = False
            self.waited = False
            self.returncode = None

        async def communicate(self):
            raise TimeoutError

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited = True
            return -9

    fake = _FakeProc()

    async def _fake_create(*_a, **_kw):
        return fake

    async def _fake_wait_for(coro, timeout):  # noqa: ARG001
        # Drive the coroutine so it raises TimeoutError as the real one would.
        await coro

    monkeypatch.setattr(system_utils.asyncio, "create_subprocess_exec", _fake_create)
    monkeypatch.setattr(system_utils.asyncio, "wait_for", _fake_wait_for)

    code, out, err = asyncio.run(system_utils.run_cmd(["sleep", "100"], timeout=1))
    assert code == -1
    assert "timed out" in err.lower()
    assert fake.killed is True, "process must be killed on timeout"
    assert fake.waited is True, "process must be reaped (awaited) after kill"


def test_run_cmd_timeout_tolerates_already_exited(monkeypatch) -> None:
    import asyncio

    class _GoneProc:
        returncode = None

        async def communicate(self):
            raise TimeoutError

        def kill(self):
            raise ProcessLookupError  # already exited between timeout and kill

        async def wait(self):
            return 0

    async def _fake_create(*_a, **_kw):
        return _GoneProc()

    async def _fake_wait_for(coro, timeout):  # noqa: ARG001
        await coro

    monkeypatch.setattr(system_utils.asyncio, "create_subprocess_exec", _fake_create)
    monkeypatch.setattr(system_utils.asyncio, "wait_for", _fake_wait_for)

    # Must not raise ProcessLookupError.
    code, _out, err = asyncio.run(system_utils.run_cmd(["sleep", "100"], timeout=1))
    assert code == -1
    assert "timed out" in err.lower()


def test_tier_hdd_critical_capacity() -> None:
    """HDD tier: capacity > 93% triggers critical."""
    result = system_utils.classify_disk_pressure_by_tier(
        storage_tier="hdd",
        percent=95.0,
        free_gb=15.0,
        io_pressure_full_avg10=10.0,
    )
    assert result["tier"] == "hdd"
    assert result["status"] == "critical"
    assert result["binding_constraint"] == "capacity"


def test_tier_hdd_io_pressure_does_not_alone_trigger_critical() -> None:
    """HDD tier: IO saturation alone should not escalate to critical (capacity is primary)."""
    result = system_utils.classify_disk_pressure_by_tier(
        storage_tier="hdd",
        percent=40.0,
        free_gb=200.0,
        io_pressure_full_avg10=80.0,
    )
    assert result["tier"] == "hdd"
    # IO can raise to at most medium for HDD when capacity is fine
    assert result["status"] in ("low", "medium")


def test_tier_ssd_behaves_like_hdd() -> None:
    """SSD tier defaults to HDD-style capacity-first pressure model."""
    result = system_utils.classify_disk_pressure_by_tier(
        storage_tier="ssd",
        percent=90.0,
        free_gb=20.0,
        io_pressure_full_avg10=5.0,
    )
    assert result["tier"] == "ssd"
    assert result["status"] in ("medium", "high", "critical")
    assert result["binding_constraint"] == "capacity"


def test_tier_unknown_falls_back_gracefully() -> None:
    """Unknown/missing tier uses safe HDD-style defaults."""
    result = system_utils.classify_disk_pressure_by_tier(
        storage_tier=None,
        percent=50.0,
        free_gb=100.0,
        io_pressure_full_avg10=5.0,
    )
    assert result["status"] == "low"
    assert "tier" in result


def test_tier_classification_includes_thresholds() -> None:
    """Result always includes the thresholds used for transparency."""
    result = system_utils.classify_disk_pressure_by_tier(
        storage_tier="nvme",
        percent=50.0,
        free_gb=100.0,
        io_pressure_full_avg10=5.0,
    )
    assert "capacity_warn_percent" in result
    assert "capacity_critical_percent" in result
    assert "io_high_threshold" in result


def test_tier_classification_status_values_are_valid() -> None:
    """Status must be one of the four defined levels."""
    valid_statuses = {"low", "medium", "high", "critical"}
    for tier in ("nvme", "hdd", "ssd", None):
        result = system_utils.classify_disk_pressure_by_tier(
            storage_tier=tier,
            percent=50.0,
            free_gb=100.0,
            io_pressure_full_avg10=5.0,
        )
        assert result["status"] in valid_statuses, f"Invalid status for tier {tier!r}: {result['status']}"


# ---------------------------------------------------------------------------
# get_host_disk_for_pool  (issue #754)
# ---------------------------------------------------------------------------


def test_get_host_disk_for_pool_from_host_drive() -> None:
    """Pool with host_drive='D:' should map to /mnt/d."""
    pool = {"storage": {"host_drive": "D:", "vhdx_path": "D:\\WSL\\ext4.vhdx"}}
    result = system_utils.get_host_disk_for_pool(pool)
    assert result == "/mnt/d"


def test_get_host_disk_for_pool_from_vhdx_path_fallback() -> None:
    """Pool with no host_drive but vhdx_path on E: maps to /mnt/e."""
    pool = {"storage": {"vhdx_path": "E:\\WSL\\ControlTower\\ext4.vhdx"}}
    result = system_utils.get_host_disk_for_pool(pool)
    assert result == "/mnt/e"


def test_get_host_disk_for_pool_nvme_pool_c_drive() -> None:
    """Pool with host_drive='C:' maps to /mnt/c (NVMe pool)."""
    pool = {"storage": {"host_drive": "C:", "disk_bus": "NVMe"}}
    result = system_utils.get_host_disk_for_pool(pool)
    assert result == "/mnt/c"


def test_get_host_disk_for_pool_no_storage_key_defaults_c() -> None:
    """Pool with no storage key at all defaults to /mnt/c."""
    result = system_utils.get_host_disk_for_pool({})
    assert result == "/mnt/c"


def test_get_host_disk_for_pool_empty_storage_defaults_c() -> None:
    """Pool with empty storage dict defaults to /mnt/c."""
    result = system_utils.get_host_disk_for_pool({"storage": {}})
    assert result == "/mnt/c"


def test_get_host_disk_for_pool_lowercase_drive() -> None:
    """Pool with lowercase drive letter 'f:' maps to /mnt/f."""
    pool = {"storage": {"host_drive": "f:"}}
    result = system_utils.get_host_disk_for_pool(pool)
    assert result == "/mnt/f"


def test_get_host_disk_for_pool_host_drive_overrides_vhdx() -> None:
    """host_drive takes precedence over vhdx_path when both present."""
    pool = {
        "storage": {
            "host_drive": "C:",
            "vhdx_path": "D:\\WSL\\ext4.vhdx",  # different drive in vhdx
        }
    }
    result = system_utils.get_host_disk_for_pool(pool)
    assert result == "/mnt/c"
