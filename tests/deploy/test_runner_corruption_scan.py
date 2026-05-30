"""Tests for deploy/runner-corruption-scan.sh.

The scan script walks ``$RUNNER_ROOT`` looking for two known corruption
signatures (see issue #651):

* Stale files under ``runner-*/_work/_temp/_runner_file_commands/`` — these
  should be cleaned at the end of every job; anything still present when the
  runner is idle is residue from a mid-job kill and will cause the next
  allocation to fail.
* ``runner-*/_diag/pages/*.log`` files older than one day — when the actions
  runner collides on a UUID, startup aborts with ``file already exists``.

The script emits Prometheus textfile-collector format to a destination file:

    runner_corruption_residue_count{host="...",runner="runner-01",kind="file_commands"} 3
    runner_corruption_residue_count{host="...",runner="runner-01",kind="diag_pages"} 1

These tests build synthetic runner trees in a tmp dir and assert the emitted
metrics file matches the expected counts.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "deploy" / "runner-corruption-scan.sh"


def _find_bash() -> str | None:
    """Locate a POSIX bash that can resolve host filesystem paths.

    On Windows, ``shutil.which("bash")`` typically returns ``C:\\Windows\\
    System32\\bash.exe`` (WSL), which sees a separate Linux filesystem and
    cannot resolve repo paths. Prefer Git Bash when it is installed.
    """
    candidates = [
        os.environ.get("BASH"),
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files\Git\bin\bash.exe",
        "/usr/bin/bash",
        "/bin/bash",
        shutil.which("bash"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            # Skip the WSL bash shim — it cannot see Windows paths.
            if candidate.lower().endswith(r"system32\bash.exe"):
                continue
            return candidate
    return None


BASH = _find_bash()

pytestmark = pytest.mark.skipif(
    BASH is None,
    reason="POSIX bash (Git Bash on Windows) is required to exercise the scan script",
)


def _as_bash_path(p: Path) -> str:
    """Translate a host path into one bash on this platform can resolve.

    On Windows / Git Bash, forward slashes are the safe form; the Git Bash
    shell otherwise interprets backslashes as escapes when arguments are
    passed through Python's subprocess.
    """
    return str(p).replace("\\", "/")


def _run_scan(runner_root: Path, prom_path: Path, host: str = "test-host") -> str:
    """Invoke the scan script and return the file's contents."""
    env = os.environ.copy()
    if BASH:
        bash_bin = str(Path(BASH).resolve().parent)
        env["PATH"] = os.pathsep.join([bash_bin, env.get("PATH", "")])
    env["RUNNER_ROOT"] = _as_bash_path(runner_root)
    env["PROM_FILE"] = _as_bash_path(prom_path)
    env["FLEET_NODE_NAME"] = host
    env["DIAG_PAGES_MIN_AGE_DAYS"] = "1"

    if os.name == "nt" and BASH:
        bash_path = Path(BASH)
        extra_paths = []
        if bash_path.parent.name == "bin":
            usr_bin = bash_path.parent.parent / "usr" / "bin"
            if usr_bin.exists():
                extra_paths.append(str(usr_bin))
        elif bash_path.parent.name == "usr" or bash_path.parent.parent.name == "usr":
            extra_paths.append(str(bash_path.parent))
        else:
            usr_bin = bash_path.parent / "usr" / "bin"
            if usr_bin.exists():
                extra_paths.append(str(usr_bin))
            usr_bin2 = bash_path.parent.parent / "usr" / "bin"
            if usr_bin2.exists():
                extra_paths.append(str(usr_bin2))

        default_git_usr = Path(r"C:\Program Files\Git\usr\bin")
        if default_git_usr.exists():
            extra_paths.append(str(default_git_usr))

        if extra_paths:
            current_path = env.get("PATH", "")
            env["PATH"] = (
                os.pathsep.join(extra_paths + [current_path]) if current_path else os.pathsep.join(extra_paths)
            )

    result = subprocess.run(
        [BASH or "bash", _as_bash_path(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"scan script failed: rc={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    return prom_path.read_text()


def _metric_value(content: str, runner: str, kind: str) -> int:
    """Extract the metric value for a given runner+kind tuple."""
    pattern = (
        r"runner_corruption_residue_count\{[^}]*"
        rf'runner="{re.escape(runner)}"[^}}]*kind="{re.escape(kind)}"[^}}]*\}}'
        r"\s+(\d+)"
    )
    match = re.search(pattern, content)
    if match is None:
        # try kind-first ordering too
        pattern2 = (
            r"runner_corruption_residue_count\{[^}]*"
            rf'kind="{re.escape(kind)}"[^}}]*runner="{re.escape(runner)}"[^}}]*\}}'
            r"\s+(\d+)"
        )
        match = re.search(pattern2, content)
    assert match is not None, f"no metric line for runner={runner} kind={kind} in:\n{content}"
    return int(match.group(1))


def test_script_exists_and_is_syntactically_valid() -> None:
    assert SCRIPT.exists(), f"scan script missing: {SCRIPT}"
    result = subprocess.run(
        [BASH or "bash", "-n", _as_bash_path(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"bash -n failed: {result.stderr}"


def test_empty_runner_root_emits_zero_metrics(tmp_path: Path) -> None:
    runner_root = tmp_path / "runners"
    runner_root.mkdir()
    prom = tmp_path / "out.prom"
    content = _run_scan(runner_root, prom)
    # Header + TYPE lines must be present even with zero runners.
    assert "# TYPE runner_corruption_residue_count gauge" in content


def test_counts_file_commands_residue(tmp_path: Path) -> None:
    runner_root = tmp_path / "runners"
    rfc = runner_root / "runner-01" / "_work" / "_temp" / "_runner_file_commands"
    rfc.mkdir(parents=True)
    (rfc / "save_state_abc").write_text("x")
    (rfc / "save_state_def").write_text("y")
    (rfc / "set_output_ghi").write_text("z")
    # An untracked file outside the residue dir must NOT be counted.
    (runner_root / "runner-01" / "_work" / "_temp" / "ignore.txt").write_text("nope")

    prom = tmp_path / "out.prom"
    content = _run_scan(runner_root, prom)
    assert _metric_value(content, "runner-01", "file_commands") == 3
    assert _metric_value(content, "runner-01", "diag_pages") == 0


def test_counts_diag_pages_older_than_one_day(tmp_path: Path) -> None:
    runner_root = tmp_path / "runners"
    diag = runner_root / "runner-02" / "_diag" / "pages"
    diag.mkdir(parents=True)
    old1 = diag / "stale1.log"
    old2 = diag / "stale2.log"
    fresh = diag / "fresh.log"
    other = diag / "ignore.txt"
    for f in (old1, old2, fresh, other):
        f.write_text("log")
    two_days_ago = time.time() - (2 * 86400)
    os.utime(old1, (two_days_ago, two_days_ago))
    os.utime(old2, (two_days_ago, two_days_ago))

    prom = tmp_path / "out.prom"
    content = _run_scan(runner_root, prom)
    assert _metric_value(content, "runner-02", "diag_pages") == 2
    assert _metric_value(content, "runner-02", "file_commands") == 0


def test_multiple_runners_are_reported_independently(tmp_path: Path) -> None:
    runner_root = tmp_path / "runners"
    for name, n_fc in (("runner-a", 1), ("runner-b", 4)):
        rfc = runner_root / name / "_work" / "_temp" / "_runner_file_commands"
        rfc.mkdir(parents=True)
        for i in range(n_fc):
            (rfc / f"f{i}").write_text("x")
    # Non-runner directories must be ignored.
    (runner_root / "notes.txt").write_text("ignore")
    (runner_root / "other-dir").mkdir()

    prom = tmp_path / "out.prom"
    content = _run_scan(runner_root, prom)
    assert _metric_value(content, "runner-a", "file_commands") == 1
    assert _metric_value(content, "runner-b", "file_commands") == 4


def _make_python_toolcache(runner_root: Path, runner: str, version: str, *, complete: bool, arch: str = "x64") -> None:
    """Build a synthetic Python tool-cache tree for one runner+version.

    Mirrors the actions/toolkit layout:
        _work/_tool/Python/<version>/<arch>/      (extracted tree)
        _work/_tool/Python/<version>/<arch>.complete (marker, present only
                                                      after a successful cache)
    """
    arch_dir = runner_root / runner / "_work" / "_tool" / "Python" / version / arch
    arch_dir.mkdir(parents=True)
    (arch_dir / "python").write_text("#!/bin/sh\n")
    if complete:
        (arch_dir.parent / f"{arch}.complete").write_text("")


def test_python_toolcache_zero_when_all_complete(tmp_path: Path) -> None:
    runner_root = tmp_path / "runners"
    _make_python_toolcache(runner_root, "runner-01", "3.11.9", complete=True)
    _make_python_toolcache(runner_root, "runner-01", "3.12.4", complete=True)

    prom = tmp_path / "out.prom"
    content = _run_scan(runner_root, prom)
    assert _metric_value(content, "runner-01", "python_toolcache") == 0


def test_python_toolcache_counts_incomplete_trees(tmp_path: Path) -> None:
    runner_root = tmp_path / "runners"
    # One good extraction, two partial ones (missing .complete marker).
    _make_python_toolcache(runner_root, "runner-01", "3.10.14", complete=True)
    _make_python_toolcache(runner_root, "runner-01", "3.11.9", complete=False)
    _make_python_toolcache(runner_root, "runner-01", "3.12.4", complete=False)

    prom = tmp_path / "out.prom"
    content = _run_scan(runner_root, prom)
    assert _metric_value(content, "runner-01", "python_toolcache") == 2
    # The other kinds must still report zero for this runner.
    assert _metric_value(content, "runner-01", "file_commands") == 0


def test_python_toolcache_absent_cache_reports_zero(tmp_path: Path) -> None:
    runner_root = tmp_path / "runners"
    # Runner exists (has a diag dir) but never provisioned a Python tool cache.
    (runner_root / "runner-07" / "_diag" / "pages").mkdir(parents=True)

    prom = tmp_path / "out.prom"
    content = _run_scan(runner_root, prom)
    assert _metric_value(content, "runner-07", "python_toolcache") == 0


def test_emits_atomic_write(tmp_path: Path) -> None:
    """The script must write via a temp file then rename for atomicity.

    Prometheus' textfile collector reads the file mid-scrape; a partial write
    would expose half-formed metrics. We assert that after the run there is
    exactly one .prom file and no stray .prom.tmp left over.
    """
    runner_root = tmp_path / "runners"
    runner_root.mkdir()
    prom = tmp_path / "out.prom"
    _run_scan(runner_root, prom)
    leftovers = list(tmp_path.glob("*.prom.tmp"))
    assert leftovers == [], f"temp files were not cleaned up: {leftovers}"
