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


def test_leased_runners_no_file() -> None:
    """_leased_runners returns empty set when leases.yml does not exist."""
    with patch.object(Path, "exists", return_value=False):
        assert samp._leased_runners() == set()
