"""Behavioral tests for the /tmp + runner-TMPDIR litter GC in
deploy/runner-cleanup.sh and for deploy/configure-runner-tmpdir.sh
(Repository_Management#1489 / #1495, durable fix).

The purge *selection* logic is exercised for real: the relevant shell
functions are sourced into a throwaway bash with stubs for the host-touching
helpers, run against a seeded scratch tree, and the survivors are asserted.
Skipped where bash/find are unavailable (native Windows lanes); the fleet
runs this on Linux self-hosted runners.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
CLEANUP = DEPLOY / "runner-cleanup.sh"
CONFIGURE = DEPLOY / "configure-runner-tmpdir.sh"

bash = shutil.which("bash")
pytestmark = pytest.mark.skipif(bash is None, reason="bash unavailable on this host")

# Entries that must be reaped once aged (top-level litter allowlist) ...
LITTER = (
    "pip-install-abc123",
    "pip-build-env-xyz",
    "pip-ephem-wheel-cache-q",
    "node-compile-cache",
    "pytest-of-runner",
    "tmpa1b2c3",  # Python tempfile default prefix
    "pymp-77x",  # multiprocessing
)
# ... and entries that must survive no matter how old they are.
KEEP = ("keep-me", "systemd-private-x", "ssh-agent-sock", "mytmp-not-prefixed")


def _bash_path(path: Path) -> str:
    """Path form the bash on this host understands (MSYS on Windows)."""
    if bash is not None and os.name == "nt":
        out = subprocess.run(
            [bash, "-c", 'cygpath -u "$1"', "_", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    return path.as_posix()


def _extract(text: str, name: str) -> str:
    start = text.index(f"{name}() {{")
    return text[start : text.index("\n}\n", start) + 3]


def _run_litter_gc(tmp_path: Path, target: Path, *, used_percent: int = 10) -> None:
    """Source cleanup_litter_in/tmp_litter_age_min from the real script with
    host helpers stubbed, then run cleanup_tmp against *target*."""
    text = CLEANUP.read_text(encoding="utf-8")
    harness = "\n".join(
        [
            "set -euo pipefail",
            "log() { :; }",
            'run() { "$@"; }',
            f"tmp_used_percent() {{ echo {used_percent}; }}",
            f'TMP_DIR="{_bash_path(target)}"',
            'TMP_LITTER_HOURS="${TMP_LITTER_HOURS:-6}"',
            'TMP_PRESSURE_PERCENT="${TMP_PRESSURE_PERCENT:-75}"',
            _extract(text, "tmp_litter_age_min"),
            _extract(text, "cleanup_litter_in"),
            _extract(text, "cleanup_tmp"),
            "cleanup_tmp",
        ]
    )
    script = tmp_path / "harness.sh"
    script.write_text(harness, encoding="utf-8", newline="\n")
    assert bash is not None
    result = subprocess.run(
        [bash, _bash_path(script)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def _seed(target: Path, names: tuple[str, ...], *, age_hours: float) -> None:
    stamp = time.time() - age_hours * 3600
    for name in names:
        d = target / name
        d.mkdir()
        (d / "payload").write_text("x", encoding="utf-8")
        os.utime(d, (stamp, stamp))


def test_aged_litter_is_reaped_and_everything_else_survives(tmp_path: Path) -> None:
    target = tmp_path / "tmp"
    target.mkdir()
    _seed(target, LITTER, age_hours=8)
    _seed(target, KEEP, age_hours=8)
    _run_litter_gc(tmp_path, target)
    survivors = sorted(p.name for p in target.iterdir())
    assert survivors == sorted(KEEP), survivors


def test_fresh_litter_survives_the_default_window(tmp_path: Path) -> None:
    """A live install's scratch is younger than TMP_LITTER_HOURS and must not
    be touched, even though its name matches the allowlist."""
    target = tmp_path / "tmp"
    target.mkdir()
    _seed(target, LITTER, age_hours=1)
    _run_litter_gc(tmp_path, target)
    assert sorted(p.name for p in target.iterdir()) == sorted(LITTER)


def test_pressure_tightens_the_window_to_30_minutes(tmp_path: Path) -> None:
    target = tmp_path / "tmp"
    target.mkdir()
    # 60 min old: inside the 6 h window, outside the 30 min pressure window.
    _seed(target, ("pip-install-old",), age_hours=1)
    # 6 min old: inside both windows.
    _seed(target, ("pip-install-new",), age_hours=0.1)
    _run_litter_gc(tmp_path, target, used_percent=80)
    assert sorted(p.name for p in target.iterdir()) == ["pip-install-new"]


def test_gc_never_recurses_below_the_top_level(tmp_path: Path) -> None:
    """Only direct children are candidates: a job's own workdir containing a
    nested `tmp*` must stay intact."""
    target = tmp_path / "tmp"
    job = target / "keep-me" / "tmpnested"
    job.mkdir(parents=True)
    stamp = time.time() - 8 * 3600
    os.utime(job, (stamp, stamp))
    os.utime(job.parent, (stamp, stamp))
    _run_litter_gc(tmp_path, target)
    assert job.is_dir()


class TestRunnerTmpdirWiring:
    """Static pins: per-runner TMPDIR GC exists, is idle-only, and runs in
    both the hourly disk-guard pass and the daily full pass."""

    @pytest.fixture(scope="class")
    def text(self) -> str:
        return CLEANUP.read_text(encoding="utf-8")

    def test_subdir_default_matches_configure_script(self, text: str) -> None:
        default = 'RUNNER_TMP_SUBDIR="${RUNNER_TMP_SUBDIR:-_work/_tmp}"'
        assert default in text
        assert default in CONFIGURE.read_text(encoding="utf-8")

    def test_runner_tmpdir_gc_skips_busy_runners(self, text: str) -> None:
        block = _extract(text, "cleanup_runner_tmpdirs")
        assert 'runner_busy "$unit" "$runner_dir"' in block
        assert "systemctl stop" not in block

    def test_runner_tmpdir_gc_runs_in_both_passes(self, text: str) -> None:
        guard_idx = text.index('if [[ "$DISK_GUARD" == "1" ]]; then')
        full_idx = text.index('elif [[ "$COMPACT_VHD_ONLY" != "1" ]]; then', guard_idx)
        else_idx = text.index("else", full_idx)
        assert "cleanup_runner_tmpdirs" in text[guard_idx:full_idx]
        assert "cleanup_runner_tmpdirs" in text[full_idx:else_idx]


class TestConfigureRunnerTmpdir:
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        assert bash is not None
        return subprocess.run(
            [bash, _bash_path(CONFIGURE), *args],
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
    def test_writes_tmpdir_line_and_creates_dir_idempotently(
        self, tmp_path: Path
    ) -> None:
        runner = self._fake_runner(tmp_path)
        (runner / ".env").write_text("KEEP=1\nTMPDIR=/tmp\n", encoding="utf-8")
        first = self._run("--runner-dir", _bash_path(runner))
        assert first.returncode == 0, first.stderr
        env = (runner / ".env").read_text(encoding="utf-8").splitlines()
        assert env.count("KEEP=1") == 1
        assert [line for line in env if line.startswith("TMPDIR=")] == [
            f"TMPDIR={_bash_path(runner)}/_work/_tmp"
        ]
        assert (runner / "_work" / "_tmp").is_dir()
        assert "1 runner(s) changed" in first.stdout
        second = self._run("--runner-dir", _bash_path(runner))
        assert "unchanged" in second.stdout
        assert "0 runner(s) changed" in second.stdout

    def test_dry_run_changes_nothing(self, tmp_path: Path) -> None:
        runner = self._fake_runner(tmp_path)
        result = self._run("--dry-run", "--runner-dir", _bash_path(runner))
        assert result.returncode == 0, result.stderr
        assert "would set TMPDIR=" in result.stdout
        assert not (runner / ".env").exists()
        assert not (runner / "_work").exists()

    def test_non_runner_dir_is_skipped(self, tmp_path: Path) -> None:
        plain = tmp_path / "not-a-runner"
        plain.mkdir()
        result = self._run("--runner-dir", _bash_path(plain))
        assert result.returncode == 0, result.stderr
        assert "not a runner directory" in result.stdout
        assert not (plain / ".env").exists()

    def test_never_restarts_units(self) -> None:
        text = CONFIGURE.read_text(encoding="utf-8")
        advice = "sudo systemctl restart 'actions.runner.*.service'"
        assert "systemctl restart" not in text.replace(advice, "")
        assert "systemctl stop" not in text
