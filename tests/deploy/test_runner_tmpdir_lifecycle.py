"""Tests for runner TMPDIR recreation and preservation (#1895).

Context — Runner_Dashboard#1895:
Self-hosted runners configure TMPDIR=<runner_dir>/_work/_tmp.
If the directory goes missing (e.g. wiped by a cleanup pass or host script),
subsequent jobs die at `mktemp` before any user code runs.

These tests ensure:
1. `deploy/runner-hooks/job-started.sh` recreates `$TMPDIR` before every job.
2. `deploy/runner-cleanup.sh: cleanup_runner_workdir` preserves `_tmp` (and `_pip-cache`)
   and never deletes them during workdir cleanup.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

_REPO = Path(__file__).resolve().parents[2]
_DEPLOY = _REPO / "deploy"
_HOOK = _DEPLOY / "runner-hooks" / "job-started.sh"
_CLEANUP = _DEPLOY / "runner-cleanup.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)


def test_job_started_recreates_missing_tmpdir(tmp_path: Path) -> None:
    """When TMPDIR is configured but does not exist, job-started.sh must create it."""
    tmpdir = tmp_path / "runners" / "runner-1" / "_work" / "_tmp"
    assert not tmpdir.exists()

    env = dict(os.environ)
    env["TMPDIR"] = as_bash_path(tmpdir)
    env["RUNNER_NAME"] = "test-runner"
    env["RUNNER_BUSY_LOCK_DIR"] = as_bash_path(tmp_path / "locks")
    env["HOME"] = as_bash_path(tmp_path / "home")

    assert BASH is not None
    result = subprocess.run(
        [BASH, as_bash_path(_HOOK)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"hook failed: stderr={result.stderr}"
    assert tmpdir.is_dir(), f"job-started.sh must create {tmpdir}"


def test_job_started_preserves_existing_tmpdir_contents(tmp_path: Path) -> None:
    """When TMPDIR already exists, job-started.sh must not delete or modify existing files."""
    tmpdir = tmp_path / "runners" / "runner-1" / "_work" / "_tmp"
    tmpdir.mkdir(parents=True)
    payload = tmpdir / "existing_file.txt"
    payload.write_text("hello", encoding="utf-8")

    env = dict(os.environ)
    env["TMPDIR"] = as_bash_path(tmpdir)
    env["RUNNER_NAME"] = "test-runner"
    env["RUNNER_BUSY_LOCK_DIR"] = as_bash_path(tmp_path / "locks")
    env["HOME"] = as_bash_path(tmp_path / "home")

    assert BASH is not None
    result = subprocess.run(
        [BASH, as_bash_path(_HOOK)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"hook failed: stderr={result.stderr}"
    assert tmpdir.is_dir()
    assert payload.read_text(encoding="utf-8") == "hello"


def test_job_started_tolerates_unset_tmpdir(tmp_path: Path) -> None:
    """When TMPDIR is unset or empty, job-started.sh must exit 0 cleanly."""
    env = dict(os.environ)
    env.pop("TMPDIR", None)
    env["RUNNER_NAME"] = "test-runner"
    env["RUNNER_BUSY_LOCK_DIR"] = as_bash_path(tmp_path / "locks")
    env["HOME"] = as_bash_path(tmp_path / "home")

    assert BASH is not None
    result = subprocess.run(
        [BASH, as_bash_path(_HOOK)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"hook failed: stderr={result.stderr}"


def _extract_func(text: str, name: str) -> str:
    start = text.index(f"{name}() {{")
    return text[start : text.index("\n}\n", start) + 3]


def test_cleanup_runner_workdir_preserves_tmpdir(tmp_path: Path) -> None:
    """cleanup_runner_workdir must reap stale repo checkouts but preserve _tmp.

    Without excluding `_tmp`, aged `_work/_tmp` directories are deleted by
    cleanup_runner_workdir when older than RUNNER_WORK_DAYS (#1895).
    """
    text = _CLEANUP.read_text(encoding="utf-8")
    runner_dir = tmp_path / "runner"
    work_dir = runner_dir / "_work"
    work_dir.mkdir(parents=True)

    # Seed entries in _work:
    # 1. Stale repo checkout (aged > 2 days) -> should be deleted
    stale_repo = work_dir / "StaleRepo"
    stale_repo.mkdir()
    (stale_repo / "file").write_text("data", encoding="utf-8")

    # 2. _tmp directory (aged > 2 days) -> must SURVIVE
    tmp_dir = work_dir / "_tmp"
    tmp_dir.mkdir()
    (tmp_dir / "litter").write_text("scratch", encoding="utf-8")

    # 3. _pip-cache directory (aged > 2 days) -> must SURVIVE (#1896)
    pip_cache = work_dir / "_pip-cache"
    pip_cache.mkdir()

    # 4. Standard excluded dirs (_temp, _tool, _actions)
    for name in ("_temp", "_tool", "_actions"):
        d = work_dir / name
        d.mkdir()

    # Backdate all entries to 10 days old
    old_time = time.time() - 10 * 86400
    for entry in work_dir.iterdir():
        os.utime(entry, (old_time, old_time))

    harness = "\n".join(
        [
            "set -euo pipefail",
            "log() { :; }",
            'delete_path() { rm -rf "$1"; }',
            "RUNNER_WORK_DAYS=2",
            "RUNNER_TEMP_DAYS=2",
            "TOOL_CACHE_DAYS=7",
            _extract_func(text, "cleanup_runner_workdir"),
            f'cleanup_runner_workdir "{as_bash_path(runner_dir)}"',
        ]
    )
    script = tmp_path / "test_cleanup.sh"
    script.write_text(harness, encoding="utf-8", newline="\n")

    assert BASH is not None
    result = subprocess.run([BASH, as_bash_path(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"cleanup failed: {result.stderr}"

    assert not stale_repo.exists(), "stale repo must be reaped"
    assert tmp_dir.is_dir(), "_tmp directory must NOT be deleted by cleanup_runner_workdir (#1895)"
    assert pip_cache.is_dir(), "_pip-cache directory must NOT be deleted by cleanup_runner_workdir (#1896)"
    assert (work_dir / "_temp").is_dir()
    assert (work_dir / "_tool").is_dir()
    assert (work_dir / "_actions").is_dir()
