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


class TestUnitMetadata:
    def test_unit_state_parses_active_and_substate(self) -> None:
        with patch("subprocess.run", return_value=_cp("ActiveState=active\nSubState=running\n")):
            assert sd._unit_state("some.service") == ("active", "running")

    def test_runner_workdir_for_unit_returns_stdout(self) -> None:
        with patch("subprocess.run", return_value=_cp("/srv/actions/runner\n")):
            assert sd._runner_workdir_for_unit("some.service") == "/srv/actions/runner"

    def test_safe_stop_contract_accepts_mixed_with_long_timeout(self) -> None:
        stdout = "KillMode=mixed\nTimeoutStopUSec=2min\n"
        with patch("subprocess.run", return_value=_cp(stdout)):
            assert sd._unit_has_safe_stop_contract("actions.runner.org.r.service") is True

    def test_safe_stop_contract_rejects_process_kill_mode(self) -> None:
        stdout = "KillMode=process\nTimeoutStopUSec=1min 30s\n"
        with patch("subprocess.run", return_value=_cp(stdout)):
            assert sd._unit_has_safe_stop_contract("actions.runner.org.r.service") is False

    def test_safe_stop_contract_rejects_short_timeout(self) -> None:
        stdout = "KillMode=mixed\nTimeoutStopUSec=90s\n"
        with patch("subprocess.run", return_value=_cp(stdout)):
            assert sd._unit_has_safe_stop_contract("actions.runner.org.r.service") is False


class TestDryRunEnabled:
    def test_module_flag_enables_dry_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sd, "DRY_RUN", True)
        assert sd._dry_run_enabled() is True

    def test_runner_autoscaler_public_flag_enables_dry_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sd, "DRY_RUN", False)
        fake_public = type("FakeRunnerAutoscaler", (), {"DRY_RUN": True})()
        monkeypatch.setitem(sd.sys.modules, "runner_autoscaler", fake_public)
        assert sd._dry_run_enabled() is True


class TestStopUnit:
    def test_happy_invokes_cleanup(self) -> None:
        with (
            patch(
                "subprocess.run",
                side_effect=[
                    _cp("KillMode=mixed\nTimeoutStopUSec=2min\n"),
                    _cp(returncode=0),
                ],
            ),
            patch("runner_state_cleanup.cleanup_runner_state") as mock_clean,
        ):
            assert sd._stop_unit("actions.runner.org.r.service") is True
            mock_clean.assert_called_once_with("actions.runner.org.r.service")

    def test_sudo_failure_still_cleans(self) -> None:
        """Even when systemctl fails, cleanup runs so the next job has a clean home.

        Regression for the fleet-wide ~/.gitconfig.lock corruption pattern
        (Runner_Dashboard#640): a SIGTERM mid-`git config --global` leaves a
        lock file behind even if systemctl reports a non-zero status because
        the runner had already died. Cleanup must run on every stop path.
        """
        with (
            patch(
                "subprocess.run",
                side_effect=[
                    _cp("KillMode=mixed\nTimeoutStopUSec=2min\n"),
                    _cp(returncode=1),
                ],
            ),
            patch("runner_state_cleanup.cleanup_runner_state") as mock_clean,
        ):
            assert sd._stop_unit("actions.runner.org.r.service") is False
            mock_clean.assert_called_once_with("actions.runner.org.r.service")

    def test_cleanup_error_does_not_break_stop(self) -> None:
        """If cleanup itself raises, _stop_unit still returns the stop status."""
        with (
            patch(
                "subprocess.run",
                side_effect=[
                    _cp("KillMode=mixed\nTimeoutStopUSec=2min\n"),
                    _cp(returncode=0),
                ],
            ),
            patch(
                "runner_state_cleanup.cleanup_runner_state",
                side_effect=RuntimeError("cleanup boom"),
            ),
        ):
            assert sd._stop_unit("actions.runner.org.r.service") is True

    def test_refuses_stop_when_unit_lacks_safe_stop_contract(self) -> None:
        with (
            patch("subprocess.run", return_value=_cp("KillMode=process\nTimeoutStopUSec=90s\n")) as mock_run,
            patch("runner_state_cleanup.cleanup_runner_state") as mock_clean,
        ):
            assert sd._stop_unit("actions.runner.org.r.service") is False
            assert mock_run.call_count == 1
            mock_clean.assert_not_called()

    def test_dry_run(self, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[name-defined]
        monkeypatch.setattr(sd, "DRY_RUN", True)
        with patch("subprocess.run") as mock_run, patch("runner_state_cleanup.cleanup_runner_state") as mock_clean:
            result = sd._stop_unit("actions.runner.org.r.service")
        mock_run.assert_not_called()
        mock_clean.assert_not_called()
        assert result is True


class TestStartUnit:
    def test_happy(self) -> None:
        with patch("subprocess.run", return_value=_cp(returncode=0)):
            assert sd._start_unit("actions.runner.org.r.service") is True

    def test_failure(self) -> None:
        with patch("subprocess.run", return_value=_cp(returncode=1)):
            assert sd._start_unit("actions.runner.org.r.service") is False
