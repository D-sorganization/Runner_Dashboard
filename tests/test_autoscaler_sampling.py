"""Smoke tests for autoscaler_sampling — resource sampling and scheduler integration.

The comprehensive sampling tests live in test_runner_autoscaler.py (imported
via the public ``runner_autoscaler`` facade).  These tests verify the sampling
module's own interface contracts.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import autoscaler_sampling as samp
import pytest


def _cp(stdout: str = "", returncode: int = 0) -> MagicMock:
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.stdout = stdout
    cp.returncode = returncode
    cp.stderr = ""
    return cp


class TestScheduledDesiredCount:
    def test_no_binary_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(samp, "RUNNER_SCHEDULER_BIN", "/nonexistent/bin/sched")
        assert samp._scheduled_desired_count(3) == 3

    def test_binary_happy(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_bin = tmp_path / "sched"
        fake_bin.touch()
        monkeypatch.setattr(samp, "RUNNER_SCHEDULER_BIN", str(fake_bin))
        with patch("subprocess.run", return_value=_cp(json.dumps({"desired": 5}))):
            assert samp._scheduled_desired_count(0) == 5

    def test_binary_failure_returns_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_bin = tmp_path / "sched"
        fake_bin.touch()
        monkeypatch.setattr(samp, "RUNNER_SCHEDULER_BIN", str(fake_bin))
        with patch("subprocess.run", return_value=_cp(returncode=1)):
            assert samp._scheduled_desired_count(7) == 7

    def test_bad_json_returns_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_bin = tmp_path / "sched"
        fake_bin.touch()
        monkeypatch.setattr(samp, "RUNNER_SCHEDULER_BIN", str(fake_bin))
        with patch("subprocess.run", return_value=_cp("not-json")):
            assert samp._scheduled_desired_count(2) == 2

    def test_oserror_returns_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_bin = tmp_path / "sched"
        fake_bin.touch()
        monkeypatch.setattr(samp, "RUNNER_SCHEDULER_BIN", str(fake_bin))
        with patch("subprocess.run", side_effect=OSError("exec failed")):
            assert samp._scheduled_desired_count(4) == 4


def test_sample_raises_without_psutil(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(samp, "psutil", None)
    with pytest.raises(RuntimeError, match="psutil is required"):
        samp._sample()


def test_sample_uses_windows_host_cpu_and_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePsutil:
        @staticmethod
        def cpu_percent(interval=0.0, percpu=False):  # noqa: ARG004
            return 4.0

        @staticmethod
        def virtual_memory():
            return type("Mem", (), {"percent": 3.0})()

        @staticmethod
        def cpu_count(logical=True):  # noqa: ARG004
            return 8

    monkeypatch.setattr(samp, "psutil", FakePsutil)
    monkeypatch.setattr(samp, "_windows_host_resource_snapshot", lambda: (42.0, 61.0))
    monkeypatch.setattr(samp.os, "getloadavg", lambda: (4.0, 0.0, 0.0), raising=False)
    monkeypatch.setattr(
        samp.shutil,
        "disk_usage",
        lambda _p: type("Disk", (), {"used": 10, "total": 100, "free": 50})(),
    )

    cpu, mem, load, disk_percent, disk_free = samp._sample()

    assert cpu == pytest.approx(42.0)
    assert mem == pytest.approx(61.0)
    assert load == pytest.approx(0.5)
    assert disk_percent == pytest.approx(10.0)
    assert disk_free > 0


def test_windows_host_snapshot_uses_absolute_powershell_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        samp.platform,
        "uname",
        lambda: type("Uname", (), {"release": "5.15.0-microsoft-standard-WSL2"})(),
    )
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args[0])
        if args[0] == "powershell.exe":
            raise OSError("PATH unavailable under systemd")
        return _cp(json.dumps({"cpu_percent": 13.0, "memory_percent": 35.0}))

    monkeypatch.setattr(samp.subprocess, "run", fake_run)

    assert samp._windows_host_resource_snapshot() == (13.0, 35.0)
    assert calls == [
        "powershell.exe",
        "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    ]


def test_leased_runners_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_leased_runners returns empty set when leases.yml does not exist."""
    monkeypatch.setenv("RUNNER_DASHBOARD_CONFIG_DIR", str(tmp_path / "missing"))
    assert samp._leased_runners() == set()


def test_leases_path_matches_lease_manager_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #932: reader path must equal the LeaseManager writer path."""
    import runner_lease as rl  # noqa: PLC0415

    monkeypatch.setenv("RUNNER_DASHBOARD_CONFIG_DIR", str(tmp_path / "cfg"))
    assert samp._leases_path() == rl._default_config_dir() / "leases.yml"


def test_leased_runners_filters_expired_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "leases.yml").write_text(
        "leases:\n"
        "  - runner_id: live-runner\n"
        "    expires_at: 9999999999\n"
        "  - runner_id: expired-runner\n"
        "    expires_at: 1\n"
        "  - runner_id: sticky-runner\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RUNNER_DASHBOARD_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(samp.time, "time", lambda: 1000.0)

    assert samp._leased_runners() == {"live-runner", "sticky-runner"}


def test_leased_runners_unparseable_yaml_returns_empty_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "leases.yml").write_text("leases:\n  - runner_id: [\n", encoding="utf-8")
    monkeypatch.setenv("RUNNER_DASHBOARD_CONFIG_DIR", str(config_dir))

    assert samp._leased_runners() == set()


def test_leased_runners_skips_one_bad_record_keeps_good(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #937c: one malformed record must not disable protection for all."""
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "leases.yml").write_text(
        "leases:\n"
        "  - runner_id: good-1\n"
        "    expires_at: 9999999999\n"
        "  - expires_at: 9999999999\n"  # missing runner_id → bad record
        "  - runner_id: good-2\n"
        "    expires_at: not-a-number\n"  # unparseable expiry → bad record
        "  - runner_id: good-3\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RUNNER_DASHBOARD_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(samp.time, "time", lambda: 1000.0)

    # good-1 and good-3 survive; the two malformed records are skipped, not fatal.
    assert samp._leased_runners() == {"good-1", "good-3"}


def test_sample_uses_fallback_load_and_root_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePsutil:
        @staticmethod
        def cpu_percent(interval=0.0, percpu=False):  # noqa: ARG004
            return 12.5

        @staticmethod
        def virtual_memory():
            return type("Mem", (), {"percent": 40.0})()

        @staticmethod
        def cpu_count(logical=True):  # noqa: ARG004
            return 0

    monkeypatch.setattr(samp, "psutil", FakePsutil)
    monkeypatch.setattr(samp, "_windows_host_resource_snapshot", lambda: None)
    monkeypatch.setattr(samp.os, "getloadavg", lambda: (_ for _ in ()).throw(OSError("unsupported")), raising=False)
    monkeypatch.setattr(samp.os.path, "exists", lambda _path: False)
    monkeypatch.setattr(
        samp.shutil,
        "disk_usage",
        lambda path: type("Disk", (), {"used": 25, "total": 100, "free": 75, "path": path})(),
    )

    cpu, mem, load, disk_percent, disk_free = samp._sample()

    assert cpu == pytest.approx(12.5)
    assert mem == pytest.approx(40.0)
    assert load == pytest.approx(0.0)
    assert disk_percent == pytest.approx(25.0)
    assert disk_free > 0


def test_parse_pressure_line_extracts_psi_metrics() -> None:
    parsed = samp._parse_pressure_line("full avg10=65.54 avg60=65.38 avg300=70.93 total=1041918528")

    assert parsed["avg10"] == pytest.approx(65.54)
    assert parsed["avg60"] == pytest.approx(65.38)
    assert parsed["avg300"] == pytest.approx(70.93)
    assert parsed["total"] == pytest.approx(1041918528)


def test_io_pressure_snapshot_reads_linux_psi(tmp_path: Path) -> None:
    pressure = tmp_path / "io"
    pressure.write_text(
        "some avg10=72.94 avg60=71.94 avg300=75.64 total=1092529985\n"
        "full avg10=65.54 avg60=65.38 avg300=70.93 total=1041918528\n",
        encoding="utf-8",
    )

    snapshot = samp._io_pressure_snapshot(str(pressure))

    assert snapshot is not None
    assert snapshot["some_avg10"] == pytest.approx(72.94)
    assert snapshot["full_avg10"] == pytest.approx(65.54)


def test_io_pressure_snapshot_returns_none_when_psi_unavailable(tmp_path: Path) -> None:
    assert samp._io_pressure_snapshot(str(tmp_path / "missing")) is None
