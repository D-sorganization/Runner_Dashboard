"""Recovery interlock defers stops when runner-busy locks show live work (#4046)."""

from __future__ import annotations

from pathlib import Path

import pytest
import runner_recovery_interlock as rri

WORKER_CMDLINE = "/home/runner/bin/Runner.Worker\0spawnclient\0"


def _fake_proc(root: Path, procs: dict[int, str]) -> Path:
    proc = root / "proc"
    proc.mkdir()
    for pid, cmdline in procs.items():
        (proc / str(pid)).mkdir()
        (proc / str(pid) / "cmdline").write_bytes(cmdline.encode())
    return proc


@pytest.mark.parametrize(
    ("body", "procs", "defer"),
    [
        ("pid=1\nworker_pid=100\n", {100: WORKER_CMDLINE}, True),
        ("pid=1\nworker_pid=100\n", {}, False),
        ("pid=1\nrunner=runner-1\n", {}, True),
        ("", {}, False),
    ],
)
def test_should_defer_runner_stop(tmp_path: Path, body: str, procs: dict[int, str], defer: bool) -> None:
    proc = _fake_proc(tmp_path, procs)
    assert rri.should_defer_runner_stop(body, proc_root=proc) is defer
