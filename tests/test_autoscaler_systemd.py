"""Smoke tests for autoscaler_systemd — unit enumeration and lifecycle helpers.

The heavy behavioural tests (stop/start, busy detection, full poll-loop) live
in test_runner_autoscaler.py which imports through the public ``runner_autoscaler``
facade.  These tests verify the systemd module's own interface contracts.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import autoscaler_systemd as sd
import pytest


def _cp(stdout: str = "", returncode: int = 0) -> MagicMock:
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.stdout = stdout
    cp.returncode = returncode
    cp.stderr = ""
    return cp


class TestListRunnerUnits:
    def test_happy_path(self) -> None:
        output = (
            "actions.runner.org.runner-a.service enabled\n"
            "actions.runner.org.runner-b.service enabled\n"
            "unrelated.service enabled\n"
        )
        with patch("subprocess.run", return_value=_cp(output)):
            units = sd._list_runner_units()
        assert "actions.runner.org.runner-a.service" in units
        assert "actions.runner.org.runner-b.service" in units
        assert "unrelated.service" not in units

    def test_empty_output(self) -> None:
        with patch("subprocess.run", return_value=_cp("")):
            assert sd._list_runner_units() == []

    def test_os_error(self) -> None:
        with patch("subprocess.run", side_effect=OSError("no systemctl")):
            assert sd._list_runner_units() == []

    def test_timeout(self) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("systemctl", 10)):
            assert sd._list_runner_units() == []


class TestRunnerNameForUnit:
    def test_standard_unit(self) -> None:
        assert (
            sd._runner_name_for_unit("actions.runner.D-sorganization.d-sorg-local-CT-3.service") == "d-sorg-local-CT-3"
        )

    def test_not_a_service_unit(self) -> None:
        assert sd._runner_name_for_unit("not-a-unit") == "not-a-unit"


class TestUnitIsActive:
    def test_active(self) -> None:
        with patch("subprocess.run", return_value=_cp(returncode=0)):
            assert sd._unit_is_active("some.service") is True

    def test_inactive(self) -> None:
        with patch("subprocess.run", return_value=_cp(returncode=1)):
            assert sd._unit_is_active("some.service") is False


class TestStopUnit:
    def test_happy(self) -> None:
        with patch("subprocess.run", return_value=_cp(returncode=0)):
            assert sd._stop_unit("actions.runner.org.r.service") is True

    def test_sudo_failure(self) -> None:
        with patch("subprocess.run", return_value=_cp(returncode=1)):
            assert sd._stop_unit("actions.runner.org.r.service") is False

    def test_dry_run(self, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[name-defined]
        monkeypatch.setattr(sd, "DRY_RUN", True)
        with patch("subprocess.run") as mock_run:
            result = sd._stop_unit("actions.runner.org.r.service")
        mock_run.assert_not_called()
        assert result is True


class TestStartUnit:
    def test_happy(self) -> None:
        with patch("subprocess.run", return_value=_cp(returncode=0)):
            assert sd._start_unit("actions.runner.org.r.service") is True

    def test_failure(self) -> None:
        with patch("subprocess.run", return_value=_cp(returncode=1)):
            assert sd._start_unit("actions.runner.org.r.service") is False
