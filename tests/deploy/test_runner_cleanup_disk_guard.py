"""Tests for the disk-pressure docker controls in deploy/runner-cleanup.sh.

Context: on 2026-05-29 the nvme WSL distro filled to 100% and every runner
crash-looped on `No space left on device`. Root cause: the daily cleanup's
disk-pressure path only shortened the runner _work/_temp retention windows; it
never pruned docker harder, and docker (build cache + volumes + buildx builders)
was the dominant consumer. These tests pin the controls that prevent recurrence:

  1. Under disk pressure, docker is pruned aggressively (all build cache incl.
     buildx, all unused images, dangling volumes) ignoring age windows.
  2. A `--disk-guard` mode reclaims docker/journal/fstrim ONLY and never bounces
     runner units, so it is safe to run on a frequent (hourly) timer.
  3. The installer ships and enables an hourly runner-disk-guard timer.

All static analysis — no bash execution, no Linux host required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
CLEANUP = DEPLOY / "runner-cleanup.sh"
INSTALLER = DEPLOY / "install-runner-maintenance.sh"


@pytest.fixture(scope="module")
def cleanup_text() -> str:
    assert CLEANUP.is_file(), f"missing {CLEANUP}"
    return CLEANUP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def installer_text() -> str:
    assert INSTALLER.is_file(), f"missing {INSTALLER}"
    return INSTALLER.read_text(encoding="utf-8")


class TestAggressiveDockerUnderPressure:
    def test_pressure_block_enables_aggressive_docker(self, cleanup_text: str) -> None:
        """When used% >= DISK_PRESSURE_PERCENT the script must flip
        DOCKER_AGGRESSIVE on, not just lower the _work retention windows."""
        assert "DISK_PRESSURE_PERCENT" in cleanup_text
        assert (
            "DOCKER_AGGRESSIVE=1" in cleanup_text
        ), "disk-pressure path must enable aggressive docker pruning"

    def test_aggressive_branch_prunes_everything(self, cleanup_text: str) -> None:
        """Aggressive mode must reclaim ALL build cache, unused images, and
        dangling volumes (no age filter), plus buildx builder caches."""
        assert 'if [[ "$DOCKER_AGGRESSIVE" == "1" ]]; then' in cleanup_text
        # All build cache (no until= filter on the aggressive path).
        assert "docker builder prune --all --force" in cleanup_text
        assert "docker buildx prune --all --force" in cleanup_text
        assert "docker image prune --all --force" in cleanup_text
        assert "docker volume prune --force" in cleanup_text

    def test_routine_path_keeps_age_window(self, cleanup_text: str) -> None:
        """Non-pressure runs must still use the DOCKER_PRUNE_UNTIL age window so
        recent build cache is preserved for build speed."""
        assert "until=${DOCKER_PRUNE_UNTIL}" in cleanup_text


class TestDiskGuardMode:
    def test_disk_guard_flag_and_env(self, cleanup_text: str) -> None:
        assert "--disk-guard)" in cleanup_text
        assert 'DISK_GUARD="${DISK_GUARD:-0}"' in cleanup_text

    def test_disk_guard_is_runner_safe(self, cleanup_text: str) -> None:
        """The disk-guard branch must NOT call cleanup_runners (which stops/starts
        idle runner units). It only reclaims docker + journal + fstrim."""
        guard_idx = cleanup_text.index('if [[ "$DISK_GUARD" == "1" ]]; then')
        elif_idx = cleanup_text.index("elif", guard_idx)
        guard_block = cleanup_text[guard_idx:elif_idx]
        assert (
            "cleanup_runners" not in guard_block
        ), "disk-guard must never bounce runner units"
        assert "cleanup_docker" in guard_block
        assert "journalctl --vacuum-size" in guard_block


class TestInstallerShipsDiskGuardTimer:
    def test_service_runs_disk_guard(self, installer_text: str) -> None:
        assert "runner-disk-guard.service" in installer_text
        assert "/usr/local/bin/runner-cleanup --disk-guard" in installer_text

    def test_timer_is_hourly(self, installer_text: str) -> None:
        assert "runner-disk-guard.timer" in installer_text
        guard_timer_idx = installer_text.index("runner-disk-guard.timer")
        # the timer heredoc with OnCalendar=hourly should appear near it
        assert (
            "OnCalendar=hourly"
            in installer_text[guard_timer_idx : guard_timer_idx + 600]
        )

    def test_timer_is_enabled(self, installer_text: str) -> None:
        enable_lines = [
            ln for ln in installer_text.splitlines() if "enable --now" in ln
        ]
        assert any(
            "runner-disk-guard.timer" in ln for ln in enable_lines
        ), "runner-disk-guard.timer must be enabled"


class TestInstallerUsesGovernedSchedulerPython:
    def test_scheduler_uses_dashboard_virtual_environment(
        self, installer_text: str
    ) -> None:
        governed_python = 'SCHEDULER_PYTHON="${SCHEDULER_PYTHON:-${HOME}/actions-runners/dashboard/.venv/bin/python}"'
        assert governed_python in installer_text
        assert (
            "ExecStart=${SCHEDULER_PYTHON} /usr/local/bin/runner-scheduler --apply"
            in installer_text
        )


class TestTmpLitterGC:
    """Pins the /tmp CI-litter GC (Repository_Management#1489 / #1495).

    Cancelled CI jobs orphan pip build dirs directly in /tmp. On hosts where
    /tmp is RAM-backed tmpfs this exhausts /tmp while every disk gate stays
    green, and all subsequent pip installs on the host die with ENOSPC —
    skipping every real quality step and hard-blocking required checks
    fleet-wide. The GC must run in BOTH the frequent disk-guard pass and the
    daily full pass, cover the observed litter patterns, and tighten its age
    window under tmp pressure.
    """

    def test_tmp_gc_config_defaults(self, cleanup_text: str) -> None:
        assert 'TMP_DIR="${TMP_DIR:-/tmp}"' in cleanup_text
        assert 'TMP_LITTER_HOURS="${TMP_LITTER_HOURS:-' in cleanup_text
        assert 'TMP_PRESSURE_PERCENT="${TMP_PRESSURE_PERCENT:-' in cleanup_text

    def test_tmp_gc_covers_observed_litter_patterns(self, cleanup_text: str) -> None:
        """Every pattern observed filling /tmp in the two incidents must be
        GC'd: pip-install-* (200MB+ each), the ephem wheel cache, build envs,
        and node-compile-cache."""
        for pattern in (
            "pip-install-*",
            "pip-ephem-wheel-cache-*",
            "pip-build-env-*",
            "pip-metadata-*",
            "pip-uninstall-*",
            "node-compile-cache",
            "pytest-of-*",
        ):
            assert f"-name '{pattern}'" in cleanup_text, f"tmp GC must cover {pattern}"

    def test_tmp_gc_is_age_windowed_and_top_level_only(self, cleanup_text: str) -> None:
        """The GC must only touch top-level /tmp entries older than the age
        window — never recurse into arbitrary trees or delete fresh files a
        live install is still writing."""
        fn_idx = cleanup_text.index("cleanup_litter_in() {")
        fn_block = cleanup_text[fn_idx : cleanup_text.index("\n}", fn_idx)]
        assert "-mindepth 1 -maxdepth 1" in fn_block
        assert '-mmin "+${age_min}"' in fn_block

    def test_tmp_pressure_tightens_age_window(self, cleanup_text: str) -> None:
        """When /tmp usage crosses TMP_PRESSURE_PERCENT the age window must
        drop so a nearly-full tmpfs is reclaimed now, not six hours later."""
        fn_idx = cleanup_text.index("tmp_litter_age_min() {")
        fn_block = cleanup_text[fn_idx : cleanup_text.index("\n}", fn_idx)]
        assert "TMP_PRESSURE_PERCENT" in fn_block
        assert "age_min=30" in fn_block

    def test_tmp_gc_runs_in_disk_guard_mode(self, cleanup_text: str) -> None:
        """The hourly disk-guard pass is the one that catches tmpfs fill
        between daily runs — cleanup_tmp must be part of it."""
        guard_idx = cleanup_text.index('if [[ "$DISK_GUARD" == "1" ]]; then')
        elif_idx = cleanup_text.index("elif", guard_idx)
        guard_block = cleanup_text[guard_idx:elif_idx]
        assert "cleanup_tmp" in guard_block

    def test_tmp_gc_runs_in_full_mode(self, cleanup_text: str) -> None:
        guard_idx = cleanup_text.index('if [[ "$DISK_GUARD" == "1" ]]; then')
        full_idx = cleanup_text.index(
            'elif [[ "$COMPACT_VHD_ONLY" != "1" ]]; then', guard_idx
        )
        full_block = cleanup_text[full_idx : cleanup_text.index("else", full_idx)]
        assert "cleanup_tmp" in full_block
