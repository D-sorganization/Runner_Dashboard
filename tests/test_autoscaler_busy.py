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
