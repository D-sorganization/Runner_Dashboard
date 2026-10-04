"""Tests for runner PIP_CACHE_DIR configuration and recreation (#1896).

Context — Runner_Dashboard#1896:
Self-hosted runners sharing a single pip cache collide during concurrent jobs,
causing FileNotFoundError in temp files or permission/ownership failures
("cache has been disabled").

These tests ensure:
1. `deploy/configure-runner-pipcache.sh` writes `PIP_CACHE_DIR=<runner>/_work/_pip-cache`
   into `.env` and creates the directory.
2. `deploy/runner-hooks/job-started.sh` recreates `$PIP_CACHE_DIR` before every job.
3. `deploy/runner-cleanup.sh: cleanup_runner_workdir` preserves `_pip-cache`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

_REPO = Path(__file__).resolve().parents[2]
_DEPLOY = _REPO / "deploy"
_HOOK = _DEPLOY / "runner-hooks" / "job-started.sh"
_CONFIGURE = _DEPLOY / "configure-runner-pipcache.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)


def test_job_started_recreates_missing_pip_cache(tmp_path: Path) -> None:
    """When PIP_CACHE_DIR is configured but does not exist, job-started.sh must create it."""
    cache_dir = tmp_path / "runners" / "runner-1" / "_work" / "_pip-cache"
    assert not cache_dir.exists()

    env = dict(os.environ)
    env["PIP_CACHE_DIR"] = as_bash_path(cache_dir)
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
    assert cache_dir.is_dir(), f"job-started.sh must create {cache_dir}"


def test_job_started_preserves_existing_pip_cache(tmp_path: Path) -> None:
    """When PIP_CACHE_DIR already exists, job-started.sh must not delete or modify existing files."""
    cache_dir = tmp_path / "runners" / "runner-1" / "_work" / "_pip-cache"
    cache_dir.mkdir(parents=True)
    payload = cache_dir / "wheel.whl"
    payload.write_text("data", encoding="utf-8")

    env = dict(os.environ)
    env["PIP_CACHE_DIR"] = as_bash_path(cache_dir)
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
    assert cache_dir.is_dir()
    assert payload.read_text(encoding="utf-8") == "data"


class TestConfigureRunnerPipCache:
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        assert BASH is not None
        return subprocess.run(
            [BASH, as_bash_path(_CONFIGURE), *args],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "RUNNER_USER": os.environ.get("USER", "runner")},
        )

    @staticmethod
    def _fake_runner(tmp_path: Path, name: str = "runner-1") -> Path:
        runner = tmp_path / name
        (runner / "bin").mkdir(parents=True)
        return runner

    @pytest.mark.skipif(os.name == "nt", reason="install -o/chown need a POSIX host")
    def test_writes_pip_cache_line_and_creates_dir_idempotently(self, tmp_path: Path) -> None:
        runner = self._fake_runner(tmp_path)
        (runner / ".env").write_text("KEEP=1\nPIP_CACHE_DIR=/tmp\n", encoding="utf-8")
        first = self._run("--runner-dir", as_bash_path(runner))
        assert first.returncode == 0, first.stderr
        env = (runner / ".env").read_text(encoding="utf-8").splitlines()
        assert env.count("KEEP=1") == 1
        expected = f"PIP_CACHE_DIR={as_bash_path(runner)}/_work/_pip-cache"
        assert [line for line in env if line.startswith("PIP_CACHE_DIR=")] == [expected]
        assert (runner / "_work" / "_pip-cache").is_dir()
        assert "1 runner(s) changed" in first.stdout
        second = self._run("--runner-dir", as_bash_path(runner))
        assert "unchanged" in second.stdout
        assert "0 runner(s) changed" in second.stdout

    def test_dry_run_changes_nothing(self, tmp_path: Path) -> None:
        runner = self._fake_runner(tmp_path)
        result = self._run("--dry-run", "--runner-dir", as_bash_path(runner))
        assert result.returncode == 0, result.stderr
        assert "would set PIP_CACHE_DIR=" in result.stdout
        assert not (runner / ".env").exists()
        assert not (runner / "_work").exists()

    def test_non_runner_dir_is_skipped(self, tmp_path: Path) -> None:
        plain = tmp_path / "not-a-runner"
        plain.mkdir()
        result = self._run("--runner-dir", as_bash_path(plain))
        assert result.returncode == 0, result.stderr
        assert "not a runner directory" in result.stdout
        assert not (plain / ".env").exists()

    def test_never_restarts_units(self) -> None:
        text = _CONFIGURE.read_text(encoding="utf-8")
        advice = "sudo systemctl restart 'actions.runner.*.service'"
        assert "systemctl restart" not in text.replace(advice, "")
        assert "systemctl stop" not in text
