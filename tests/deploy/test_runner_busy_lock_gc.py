"""Behavioural tests for the runner-busy lock GC (#1987, review on PR #1989).

The JOB_STARTED hook records ``worker_pid=`` (its ``Runner.Worker`` ancestor)
next to the legacy ``pid=`` (the short-lived hook shell, dead for the whole
job). A lock is stale-by-pid ONLY when ``worker_pid`` is present and that pid
is dead or no longer a ``Runner.Worker`` (pid reuse). A lock without
``worker_pid`` falls back to the age rule; a dead hook ``pid=`` never deletes
it.

These drive the real bash functions against a fake proc root
(``PROC_ROOT``). Harness safety: only the function definitions of
runner-cleanup.sh are sourced (never ``main``), and ``docker_shim_prelude``
pins docker to a stub with ``DOCKER_HOST`` pointing at a socket that does not
exist, because on the Windows dev host DOCKER_HOST is a live CI daemon.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path, docker_shim_prelude

ROOT = Path(__file__).resolve().parents[2]
CLEANUP = ROOT / "deploy" / "runner-cleanup.sh"
HOOK = ROOT / "deploy" / "runner-hooks" / "job-started.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)

WORKER_CMDLINE = "/home/runner/actions-runners/runner-4/bin/Runner.Worker\0spawnclient\0123\0456\0"
LISTENER_CMDLINE = "/home/runner/actions-runners/runner-4/bin/Runner.Listener\0run\0"


def _fake_proc(root: Path, procs: dict[int, tuple[str, int]]) -> Path:
    """Build ``root/proc/<pid>/{cmdline,status}`` from ``{pid: (cmdline, ppid)}``."""
    proc = root / "proc"
    proc.mkdir()
    for pid, (cmdline, ppid) in procs.items():
        d = proc / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes(cmdline.encode())
        (d / "status").write_text(f"Name:\tx\nPid:\t{pid}\nPPid:\t{ppid}\n", encoding="utf-8", newline="\n")
    return proc


def _cleanup_functions() -> str:
    """runner-cleanup.sh up to (not including) the EXIT trap and main()."""
    return CLEANUP.read_text(encoding="utf-8").split("# Emit a textfile-collector metric", 1)[0]


def _run_cleanup_fn(tmp_path: Path, proc: Path, body: str) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
    docker.chmod(0o755)
    harness = tmp_path / "harness.sh"
    harness.write_text(
        docker_shim_prelude(bin_dir) + _cleanup_functions() + "\n" + body + "\n",
        encoding="utf-8",
        newline="\n",
    )
    env = os.environ.copy()
    env.update(
        {
            "DOCKER_HOST": "unix:///nonexistent/docker-test-guard.sock",
            "PROC_ROOT": as_bash_path(proc),
            "RUNNER_BUSY_LOCK_DIR": as_bash_path(tmp_path / "locks"),
            "LOG_DIR": as_bash_path(tmp_path / "logs"),
            "USER": "runner-test",
        }
    )
    return subprocess.run([BASH, as_bash_path(harness)], capture_output=True, text=True, check=False, env=env)


# (lock body, fake procs, expected busy, expected lock kept)
CASES = {
    "live_worker_kept": ("pid=11\nworker_pid=100\n", {100: (WORKER_CMDLINE, 1)}, True),
    "dead_worker_pid_removed": ("pid=11\nworker_pid=100\n", {}, False),
    "reused_pid_removed": ("pid=11\nworker_pid=100\n", {100: ("/usr/bin/python3\0-m\0http.server\0", 1)}, False),
    "legacy_dead_hook_pid_kept": ("pid=11\nrunner=r\n", {}, True),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_runner_busy_via_lockfile(tmp_path: Path, case: str) -> None:
    body, procs, busy = CASES[case]
    proc = _fake_proc(tmp_path, procs)
    locks = tmp_path / "locks"
    locks.mkdir()
    lock = locks / "d-sorg-local-CT-4.lock"
    lock.write_text(body, encoding="utf-8", newline="\n")

    result = _run_cleanup_fn(
        tmp_path, proc, "if runner_busy_via_lockfile d-sorg-local-CT-4; then echo BUSY; else echo IDLE; fi"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == ("BUSY" if busy else "IDLE"), result.stdout
    assert lock.exists() is busy


def test_cleanup_stale_busy_locks_only_removes_stale_by_worker_pid(tmp_path: Path) -> None:
    proc = _fake_proc(tmp_path, {100: (WORKER_CMDLINE, 1), 200: ("/usr/bin/sleep\060\0", 1)})
    locks = tmp_path / "locks"
    locks.mkdir()
    for name, body in {
        "live": "pid=11\nworker_pid=100\n",
        "dead": "pid=12\nworker_pid=300\n",
        "reused": "pid=13\nworker_pid=200\n",
        "legacy": "pid=14\n",
    }.items():
        (locks / f"{name}.lock").write_text(body, encoding="utf-8", newline="\n")

    result = _run_cleanup_fn(tmp_path, proc, "cleanup_stale_busy_locks")

    assert result.returncode == 0, result.stderr
    assert sorted(p.stem for p in locks.glob("*.lock")) == ["legacy", "live"]


def test_age_rule_still_removes_old_legacy_lock(tmp_path: Path) -> None:
    proc = _fake_proc(tmp_path, {})
    locks = tmp_path / "locks"
    locks.mkdir()
    lock = locks / "old.lock"
    lock.write_text("pid=14\n", encoding="utf-8", newline="\n")
    old = time.time() - 3 * 86400
    os.utime(lock, (old, old))

    result = _run_cleanup_fn(tmp_path, proc, "cleanup_stale_busy_locks")

    assert result.returncode == 0, result.stderr
    assert not lock.exists()


def test_missing_proc_root_never_deletes(tmp_path: Path) -> None:
    """No proc filesystem → liveness is unknowable → keep the lock."""
    locks = tmp_path / "locks"
    locks.mkdir()
    lock = locks / "r.lock"
    lock.write_text("pid=11\nworker_pid=100\n", encoding="utf-8", newline="\n")

    result = _run_cleanup_fn(tmp_path, tmp_path / "no-such-proc", "cleanup_stale_busy_locks")

    assert result.returncode == 0, result.stderr
    assert lock.exists()


# --- job-started.sh: worker_pid discovery ------------------------------------


def _hook_function(name: str) -> str:
    text = HOOK.read_text(encoding="utf-8")
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL)
    assert match, f"{name}() not found in job-started.sh"
    return match.group(0)


def _find_worker(tmp_path: Path, proc: Path, start: int) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    harness = tmp_path / "hook-fn.sh"
    harness.write_text(
        "set -u\n" + _hook_function("_find_worker_pid") + f"_find_worker_pid {start} || echo NONE\n",
        encoding="utf-8",
        newline="\n",
    )
    env = os.environ.copy()
    env["PROC_ROOT"] = as_bash_path(proc)
    return subprocess.run([BASH, as_bash_path(harness)], capture_output=True, text=True, check=False, env=env)


def test_hook_finds_runner_worker_ancestor(tmp_path: Path) -> None:
    # hook shell 400 <- sh 300 <- Runner.Worker 200 <- Runner.Listener 100
    proc = _fake_proc(
        tmp_path,
        {
            100: (LISTENER_CMDLINE, 1),
            200: (WORKER_CMDLINE, 100),
            300: ("/bin/sh\0-c\0hook\0", 200),
            400: ("/bin/bash\0job-started.sh\0", 300),
        },
    )
    result = _find_worker(tmp_path, proc, 400)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "200"


def test_hook_writes_no_worker_pid_without_worker_ancestor(tmp_path: Path) -> None:
    proc = _fake_proc(tmp_path, {100: (LISTENER_CMDLINE, 1), 300: ("/bin/sh\0", 100)})
    result = _find_worker(tmp_path, proc, 300)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "NONE"


def test_hook_lock_body_keeps_pid_and_adds_worker_pid() -> None:
    text = HOOK.read_text(encoding="utf-8")
    assert "printf 'pid=%s\\n' \"$$\"" in text, "legacy pid= must stay for compatibility"
    assert re.search(r'_find_worker_pid "\$\{?PPID', text), "hook must start the ancestor walk at $PPID"
    assert "worker_pid=" in text
