"""Behavioural tests for the runner-cleanup knobs the storage-GC profiles rely on (#1925).

* ``cleanup_rustup_tmp`` — the reaper whose absence leaked 35k dirs (23 GB) on
  OGLaptop and 9k on DeskComputer; previously only string-asserted.
* ``CLEANUP_TMP=0`` — workstation hosts must not have /tmp/tmp* reaped under a
  logged-in user's running apps.
* ``PRUNE_DOCKER_CONTAINERS=0`` — a workstation's stopped containers are user
  state, not CI litter.

Functions are sourced from the real script with host helpers stubbed (same
harness pattern as test_runner_tmp_litter_gc.py).
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

CLEANUP = Path(__file__).resolve().parents[2] / "deploy" / "runner-cleanup.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)


def _extract(text: str, name: str) -> str:
    start = text.index(f"{name}() {{")
    return text[start : text.index("\n}\n", start) + 3]


def _harness(tmp_path: Path, lines: list[str]) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "harness.sh"
    body = "\n".join(["set -euo pipefail", 'log() { echo "LOG $*"; }', *lines])
    script.write_text(body, encoding="utf-8", newline="\n")
    assert BASH is not None
    return subprocess.run([BASH, as_bash_path(script)], capture_output=True, text=True, check=False)


def _seed(parent: Path, names: tuple[str, ...], *, age_hours: float) -> None:
    stamp = time.time() - age_hours * 3600
    for name in names:
        d = parent / name
        d.mkdir(parents=True)
        (d / "payload").write_text("x", encoding="utf-8")
        os.utime(d, (stamp, stamp))


# --- rustup tmp reaper ------------------------------------------------------

OLD = ("0039580hcibe_gos_dir", "008k5p5us44dfx0z_dir")
FRESH = ("zz_live_install_dir",)


def _rustup_gc(tmp_path: Path, home: Path, *, rustup_running: bool = False, dry_run: str = "0") -> str:
    text = CLEANUP.read_text(encoding="utf-8")
    result = _harness(
        tmp_path,
        [
            f'getent() {{ echo "ci:x:1000:1000::{as_bash_path(home)}:/bin/bash"; }}',
            f"pgrep() {{ return {0 if rustup_running else 1}; }}",
            "RUNNER_USER=ci",
            "RUSTUP_TMP_HOURS=6",
            f"DRY_RUN={dry_run}",
            _extract(text, "cleanup_rustup_tmp"),
            "cleanup_rustup_tmp",
        ],
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_rustup_reaper_removes_only_aged_entries(tmp_path: Path) -> None:
    tmp = tmp_path / "home/.rustup/tmp"
    _seed(tmp, OLD, age_hours=30)
    _seed(tmp, FRESH, age_hours=1)
    out = _rustup_gc(tmp_path, tmp_path / "home")
    assert sorted(p.name for p in tmp.iterdir()) == sorted(FRESH)
    assert "reaping 2 entries" in out


def test_rustup_reaper_never_runs_while_rustup_is_live(tmp_path: Path) -> None:
    tmp = tmp_path / "home/.rustup/tmp"
    _seed(tmp, OLD, age_hours=30)
    out = _rustup_gc(tmp_path, tmp_path / "home", rustup_running=True)
    assert sorted(p.name for p in tmp.iterdir()) == sorted(OLD)
    assert "rustup is running" in out


def test_rustup_reaper_dry_run_reports_without_deleting(tmp_path: Path) -> None:
    tmp = tmp_path / "home/.rustup/tmp"
    _seed(tmp, OLD, age_hours=30)
    _rustup_gc(tmp_path, tmp_path / "home", dry_run="1")
    assert sorted(p.name for p in tmp.iterdir()) == sorted(OLD)


def test_rustup_reaper_tolerates_missing_dir(tmp_path: Path) -> None:
    _rustup_gc(tmp_path, tmp_path / "nohome")


# --- CLEANUP_TMP gate ---------------------------------------------------------


def _tmp_gc(tmp_path: Path, target: Path, cleanup_tmp: str | None) -> str:
    text = CLEANUP.read_text(encoding="utf-8")
    lines = [
        'run() { "$@"; }',
        "tmp_used_percent() { echo 10; }",
        f'TMP_DIR="{as_bash_path(target)}"',
        "DRY_RUN=0",
        "TMP_LITTER_HOURS=6",
        "TMP_PRESSURE_PERCENT=75",
    ]
    if cleanup_tmp is not None:
        lines.append(f"CLEANUP_TMP={cleanup_tmp}")
    lines += [
        _extract(text, "tmp_litter_age_min"),
        _extract(text, "cleanup_litter_in"),
        _extract(text, "cleanup_tmp"),
        "cleanup_tmp",
    ]
    result = _harness(tmp_path, lines)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_cleanup_tmp_zero_leaves_tmp_untouched(tmp_path: Path) -> None:
    target = tmp_path / "tmp"
    _seed(target, ("tmpa1b2c3", "pip-install-x"), age_hours=48)
    out = _tmp_gc(tmp_path, target, "0")
    assert sorted(p.name for p in target.iterdir()) == ["pip-install-x", "tmpa1b2c3"]
    assert "tmp GC disabled" in out


def test_cleanup_tmp_defaults_on(tmp_path: Path) -> None:
    """The runner default is unchanged: an unset knob still reaps litter."""
    target = tmp_path / "tmp"
    _seed(target, ("tmpa1b2c3",), age_hours=48)
    _tmp_gc(tmp_path, target, None)
    assert list(target.iterdir()) == []


def test_cleanup_tmp_default_is_declared_at_top_level() -> None:
    text = CLEANUP.read_text(encoding="utf-8")
    assert 'CLEANUP_TMP="${CLEANUP_TMP:-1}"' in text
    assert 'PRUNE_DOCKER_CONTAINERS="${PRUNE_DOCKER_CONTAINERS:-1}"' in text


# --- PRUNE_DOCKER_CONTAINERS gate ---------------------------------------------


def _docker_calls(tmp_path: Path, prune_containers: str | None) -> list[str]:
    text = CLEANUP.read_text(encoding="utf-8")
    calls = tmp_path / "docker.log"
    lines = [
        f'docker() {{ echo "$*" >> "{as_bash_path(calls)}"; }}',
        'run() { "$@"; }',
        "DOCKER_AGGRESSIVE=0",
        "DOCKER_PRUNE_UNTIL=168h",
        "DOCKER_DANGLING_UNTIL=6h",
        "PRUNE_DOCKER_VOLUMES=0",
        "PRUNE_ORPHAN_BUILDX_STATE=0",
        "DRY_RUN=0",
    ]
    if prune_containers is not None:
        lines.append(f"PRUNE_DOCKER_CONTAINERS={prune_containers}")
    lines += [_extract(text, "cleanup_docker"), "cleanup_docker"]
    result = _harness(tmp_path, lines)
    assert result.returncode == 0, result.stderr
    return calls.read_text(encoding="utf-8").splitlines()


def test_prune_docker_containers_zero_skips_container_prune(tmp_path: Path) -> None:
    calls = _docker_calls(tmp_path, "0")
    assert not any(c.startswith("container prune") for c in calls), calls
    assert any(c.startswith("builder prune") for c in calls), calls


def test_container_prune_defaults_on(tmp_path: Path) -> None:
    calls = _docker_calls(tmp_path, None)
    assert any(c.startswith("container prune") for c in calls), calls
