"""Smoke tests for autoscaler_busy — layered runner busy-detection.

The comprehensive busy-detection tests (Strategy 1-4, edge cases, short-circuit
ordering) live in test_runner_autoscaler.py, which uses the public
``runner_autoscaler`` facade.  These tests verify the busy module is importable
and that its public helpers are accessible at their canonical paths.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import autoscaler_busy as busy
import pytest

UNIT = "actions.runner.D-sorganization.runner-1.service"


class TestBusyViaLockfile:
    def test_no_lockfile_returns_false(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]
    ) -> None:
        monkeypatch.setattr(busy, "RUNNER_BUSY_LOCK_DIR", tmp_path)
        assert busy._runner_busy_via_lockfile(UNIT) is False

    def test_fresh_lockfile_returns_true(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]
    ) -> None:
        monkeypatch.setattr(busy, "RUNNER_BUSY_LOCK_DIR", tmp_path)
        (tmp_path / "runner-1.lock").write_text("pid=1\n")
        assert busy._runner_busy_via_lockfile(UNIT) is True

    def test_stale_lockfile_returns_false(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]
    ) -> None:
        import os as _os

        monkeypatch.setattr(busy, "RUNNER_BUSY_LOCK_DIR", tmp_path)
        monkeypatch.setattr(busy, "RUNNER_BUSY_LOCK_MAX_AGE_SECONDS", 60)
        lock = tmp_path / "runner-1.lock"
        lock.write_text("pid=1\n")
        old = time.time() - 3600
        _os.utime(lock, (old, old))
        assert busy._runner_busy_via_lockfile(UNIT) is False


class TestBusyViaPickupDir:
    def test_no_workdir_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]
    ) -> None:
        monkeypatch.setattr(busy, "_runner_workdir_for_unit", lambda _u: "")
        assert busy._runner_busy_via_pickup_dir(UNIT) is False

    def test_fresh_pickup_dir_returns_true(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]
    ) -> None:
        monkeypatch.setattr(busy, "_runner_workdir_for_unit", lambda _u: str(tmp_path))
        fc = tmp_path / "_work" / "_temp" / "_runner_file_commands"
        fc.mkdir(parents=True)
        assert busy._runner_busy_via_pickup_dir(UNIT) is True

    def test_stale_pickup_dir_returns_false(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]
    ) -> None:
        import os as _os

        monkeypatch.setattr(busy, "_runner_workdir_for_unit", lambda _u: str(tmp_path))
        monkeypatch.setattr(busy, "RUNNER_PICKUP_DIR_MAX_AGE_SECONDS", 10)
        fc = tmp_path / "_work" / "_temp" / "_runner_file_commands"
        fc.mkdir(parents=True)
        old = time.time() - 600
        _os.utime(fc, (old, old))
        assert busy._runner_busy_via_pickup_dir(UNIT) is False


def test_runner_is_busy_short_circuits_on_pickup_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]
) -> None:
    """Strategy 1 must short-circuit before reaching subprocess (MainPID path)."""
    monkeypatch.setattr(busy, "_runner_workdir_for_unit", lambda _u: str(tmp_path))
    fc = tmp_path / "_work" / "_temp" / "_runner_file_commands"
    fc.mkdir(parents=True)
    with patch("subprocess.run", side_effect=AssertionError("MainPID path reached")):
        assert busy._runner_is_busy(UNIT) is True


def test_runner_is_busy_treats_mainpid_timeout_as_busy(
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]
) -> None:
    """Issue #937a: a TimeoutExpired querying MainPID must NOT abort the tick.

    The query is treated as 'busy' (fail safe — never stop a runner we cannot
    confirm idle) and the exception is swallowed so the poll loop's broad except
    is never reached and the other pools' scaling still runs.
    """
    # Force Strategies 1 and 2 to miss so control reaches the MainPID query.
    monkeypatch.setattr(busy, "_runner_busy_via_pickup_dir_public", lambda _u: False)
    monkeypatch.setattr(busy, "_runner_busy_via_lockfile", lambda _u: False)
    import subprocess as _sp  # noqa: PLC0415

    with patch("subprocess.run", side_effect=_sp.TimeoutExpired("systemctl", 5)):
        # Must return True (busy) rather than propagating TimeoutExpired.
        assert busy._runner_is_busy(UNIT) is True


class _FakeProc:
    """Minimal psutil.Process stand-in exposing the .info dict process_iter sets."""

    def __init__(self, cmdline: list[str]) -> None:
        self.info = {"cmdline": cmdline}


class _FakePsutil:
    NoSuchProcess = ProcessLookupError
    AccessDenied = PermissionError

    def __init__(self, procs: list[_FakeProc]) -> None:
        self._procs = procs

    def process_iter(self, _attrs: list[str] | None = None) -> list[_FakeProc]:
        return list(self._procs)


class TestBusyViaWorkerScan:
    """Strategy 3b — global Runner.Worker scan keyed on the runner's workdir."""

    WORKDIR = "/home/runner/actions-runners-nvme/runner-4"

    def test_no_workdir_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[name-defined]
        monkeypatch.setattr(busy, "_runner_workdir_for_unit", lambda _u: "")
        assert busy._runner_busy_via_worker_scan(UNIT) is False

    def test_no_psutil_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[name-defined]
        monkeypatch.setattr(busy, "_runner_workdir_for_unit", lambda _u: self.WORKDIR)
        monkeypatch.setattr(busy, "psutil", None)
        assert busy._runner_busy_via_worker_scan(UNIT) is False

    def test_live_worker_for_workdir_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[name-defined]
        """A live Runner.Worker whose exec path is <workdir>/bin/Runner.Worker → busy.

        Regression for PR #813: the OCI-export Worker that the MainPID child
        walk missed is still ground-truth evidence the runner is busy.
        """
        monkeypatch.setattr(busy, "_runner_workdir_for_unit", lambda _u: self.WORKDIR)
        fake = _FakePsutil(
            [
                _FakeProc(["/usr/bin/python3", "something"]),
                _FakeProc([f"{self.WORKDIR}/bin/Runner.Worker", "spawnclient", "154", "157"]),
            ]
        )
        monkeypatch.setattr(busy, "psutil", fake)
        assert busy._runner_busy_via_worker_scan(UNIT) is True

    def test_worker_for_other_runner_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[name-defined]
        """A Worker belonging to a *different* runner must not mark this one busy."""
        monkeypatch.setattr(busy, "_runner_workdir_for_unit", lambda _u: self.WORKDIR)
        other = "/home/runner/actions-runners-nvme/runner-9"
        fake = _FakePsutil([_FakeProc([f"{other}/bin/Runner.Worker", "spawnclient", "1", "2"])])
        monkeypatch.setattr(busy, "psutil", fake)
        assert busy._runner_busy_via_worker_scan(UNIT) is False

    def test_process_iter_error_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[name-defined]
        """Busy-detection must never raise: a scan failure yields False, not an error."""
        monkeypatch.setattr(busy, "_runner_workdir_for_unit", lambda _u: self.WORKDIR)

        class _BoomPsutil(_FakePsutil):
            def process_iter(self, _attrs: list[str] | None = None) -> list[_FakeProc]:
                raise RuntimeError("psutil exploded")

        monkeypatch.setattr(busy, "psutil", _BoomPsutil([]))
        assert busy._runner_busy_via_worker_scan(UNIT) is False
