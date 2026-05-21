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


def test_leased_runners_no_file() -> None:
    """_leased_runners returns empty set when leases.yml does not exist."""
    with patch.object(Path, "exists", return_value=False):
        assert samp._leased_runners() == set()
