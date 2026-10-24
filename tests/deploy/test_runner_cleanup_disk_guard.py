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

Most checks are static; the dangling-image contract also drives the real bash
function with a stub Docker executable, so no Docker daemon or Linux host is required.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path, docker_shim_prelude

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
        assert "DOCKER_AGGRESSIVE=1" in cleanup_text, "disk-pressure path must enable aggressive docker pruning"
        assert 'host_mnt="${WSL_HOST_MOUNT:-/mnt/c}"' in cleanup_text

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

    def test_dangling_images_use_short_window(self, cleanup_text: str) -> None:
        """2026-09-14: 42 dangling upstream-drift images (~100 GiB, all under
        41 h old) sat under the 168h window and grew the ControlTower vhdx by
        33 GB in nine days. Dangling images are superseded by definition, so
        the routine path must prune them on a much shorter window than build
        cache, without touching tagged images (no --all)."""
        assert 'DOCKER_DANGLING_UNTIL="${DOCKER_DANGLING_UNTIL:-6h}"' in cleanup_text
        anchor = '    else\n        if [[ "$prune_containers" == "1" ]]; then run docker container prune'
        routine = cleanup_text[cleanup_text.index(anchor) :]
        routine = routine[: routine.index("\n    fi\n")]
        assert 'docker image prune --force --filter dangling=true --filter "until=${DOCKER_DANGLING_UNTIL}"' in routine
        assert "docker image prune --all" not in routine

    def test_rustup_tmp_gc_is_guarded(self, cleanup_text: str) -> None:
        """19,627 leaked ~/.rustup/tmp/*_dir entries (37 GiB) accumulated in 17
        days from jobs killed mid-rustup. The GC must be age-windowed, skip
        while rustup runs, and only run on the full daily pass."""
        assert 'RUSTUP_TMP_HOURS="${RUSTUP_TMP_HOURS:-6}"' in cleanup_text
        fn = cleanup_text[cleanup_text.index("cleanup_rustup_tmp() {") :]
        fn = fn[: fn.index("\n}\n")]
        assert "pgrep -x rustup" in fn
        assert '-mmin "+$(( RUSTUP_TMP_HOURS * 60 ))"' in fn
        assert "-maxdepth 1" in fn
        guard_idx = cleanup_text.index('if [[ "$DISK_GUARD" == "1" ]]; then')
        elif_idx = cleanup_text.index("elif", guard_idx)
        assert "cleanup_rustup_tmp" not in cleanup_text[guard_idx:elif_idx]
        assert "cleanup_rustup_tmp\n        cleanup_common_caches" in cleanup_text

    def test_orphan_buildx_state_volumes_are_reaped(self, cleanup_text: str) -> None:
        """docker/setup-buildx-action leaves one buildx_buildkit_<id>_state
        volume per job; DeskComputer had 37 unreferenced ones (119 GiB) on
        2026-09-14 because PRUNE_DOCKER_VOLUMES=0 skips every volume. The
        reaper must be name-scoped AND dangling-scoped so user volumes and a
        builder mid-job are never removed."""
        assert 'PRUNE_ORPHAN_BUILDX_STATE="${PRUNE_ORPHAN_BUILDX_STATE:-1}"' in cleanup_text
        fn = cleanup_text[cleanup_text.index("cleanup_orphan_buildx_state() {") :]
        fn = fn[: fn.index("\n}\n")]
        assert "--filter dangling=true" in fn
        assert "--filter name=buildx_buildkit_" in fn
        assert "docker volume prune" not in fn
        assert 'b="${b%0_state}"' in fn, "only builders whose state volume was reaped are removed"


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
        assert "cleanup_runners" not in guard_block, "disk-guard must never bounce runner units"
        assert "cleanup_docker" in guard_block
        assert "journalctl --vacuum-size" in guard_block


class TestInstallerDelegatesStorageGc:
    """The disk-guard service/timer are owned by install-storage-gc.sh (#1925);
    their content and enablement are tested behaviourally in
    test_install_storage_gc.py. Here: the maintenance installer must delegate,
    never carry a second copy that can drift."""

    def test_delegates_to_the_single_owner(self, installer_text: str) -> None:
        assert 'install-storage-gc.sh" --profile runner' in installer_text

    def test_does_not_redefine_storage_gc_units(self, installer_text: str) -> None:
        assert "ExecStart=/usr/local/bin/runner-cleanup" not in installer_text
        assert "/etc/systemd/system/runner-disk-guard" not in installer_text
        assert "/etc/systemd/system/runner-cleanup.service >" not in installer_text
        assert 'runner-cleanup.sh" /usr/local/bin/runner-cleanup' not in installer_text


class TestInstallerUsesGovernedSchedulerPython:
    def test_scheduler_uses_dashboard_virtual_environment(self, installer_text: str) -> None:
        governed_python = 'SCHEDULER_PYTHON="${SCHEDULER_PYTHON:-${HOME}/actions-runners/dashboard/.venv/bin/python}"'
        assert governed_python in installer_text
        assert "ExecStart=${SCHEDULER_PYTHON} /usr/local/bin/runner-scheduler --apply" in installer_text


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
        full_idx = cleanup_text.index('elif [[ "$COMPACT_VHD_ONLY" != "1" ]]; then', guard_idx)
        full_block = cleanup_text[full_idx : cleanup_text.index("else", full_idx)]
        assert "cleanup_tmp" in full_block


def test_all_image_prune_spares_keep_labelled_images(cleanup_text: str) -> None:
    """Tagged images labelled ``keep`` (e.g. the UpstreamDrift explorer / ud-sim images)
    must survive disk-pressure prune; a plain `image prune --all` deleted them
    repeatedly (UpstreamDrift #12878). Every `--all` prune must carry label!=keep."""
    all_prune_lines = [line for line in cleanup_text.splitlines() if "docker image prune" in line and "--all" in line]
    assert all_prune_lines, "expected docker image prune --all calls"
    for line in all_prune_lines:
        assert "label!=keep" in line, f"aggressive image prune does not spare keep: {line.strip()}"


def test_dangling_image_prune_reclaims_without_label_filter(cleanup_text: str) -> None:
    """Untagged (dangling) images must be pruned without `label!=keep` (#1981).
    Superseded builds of keep-labelled images retain the label when untagged,
    so filtering by `label!=keep` permanently leaks them."""
    dangling_prune_lines = [
        line for line in cleanup_text.splitlines() if "docker image prune" in line and "--all" not in line
    ]
    assert dangling_prune_lines, "expected dangling docker image prune calls"
    for line in dangling_prune_lines:
        assert "label!=keep" not in line, f"dangling image prune must not filter out keep: {line.strip()}"


@pytest.mark.skipif(BASH is None, reason=SKIP_REASON)
@pytest.mark.parametrize("aggressive", ["0", "1"])
def test_dangling_prune_command_uses_age_window_in_both_modes(tmp_path: Path, aggressive: str) -> None:
    """Run the real prune function with a stub Docker and inspect its argv."""
    assert BASH is not None
    docker_dir = tmp_path / "bin"
    docker_dir.mkdir()
    calls = tmp_path / "docker-calls.log"
    docker = docker_dir / "docker"
    docker.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$DOCKER_CALLS"\nexit 0\n',
        encoding="utf-8",
        newline="\n",
    )
    docker.chmod(0o755)

    script = CLEANUP.read_text(encoding="utf-8").split("# Emit a textfile-collector metric", 1)[0]
    script = docker_shim_prelude(docker_dir) + script + "\ncleanup_docker\n"
    harness = tmp_path / "cleanup-docker.sh"
    harness.write_text(script, encoding="utf-8", newline="\n")
    env = os.environ.copy()
    env.update(
        {
            "DOCKER_HOST": "unix:///nonexistent/docker-test-guard.sock",
            "DOCKER_CALLS": as_bash_path(calls),
            "DOCKER_AGGRESSIVE": aggressive,
            "DOCKER_DANGLING_UNTIL": "2h",
            "PRUNE_ORPHAN_BUILDX_STATE": "0",
            "LOG_DIR": as_bash_path(tmp_path / "logs"),
            "USER": "runner-test",
        }
    )
    result = subprocess.run([BASH, as_bash_path(harness)], capture_output=True, text=True, check=False, env=env)

    assert result.returncode == 0, result.stderr
    docker_calls = calls.read_text(encoding="utf-8").splitlines()
    assert "image prune --force --filter dangling=true --filter until=2h" in docker_calls
    assert not any(
        call.startswith("image prune") and "--all" not in call and "--filter dangling=true" not in call
        for call in docker_calls
    )
    assert all("label!=keep" not in call for call in docker_calls if call.startswith("image prune --force"))
    if aggressive == "1":
        all_prunes = [call for call in docker_calls if call.startswith("image prune") and "--all" in call]
        assert all_prunes, "aggressive cleanup must include an all-unused-image prune"
        assert all("label!=keep" in call for call in all_prunes)
