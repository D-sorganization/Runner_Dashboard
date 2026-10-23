"""Run one untrusted child process under the scan's stop conditions (#2572, #2603).

Both phases stream untrusted output: phase A a repository's build, phase B the
agent's event stream. :func:`watch_process` bounds each line and the backlog
(panel q5) and kills the whole process tree on cancel, timeout, silence, a
failed runtime poll, or any error.
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

# Child output is untrusted (a repository's build, the agent's stream): one
# line and the backlog are both bounded so it cannot exhaust host memory.
MAX_LINE_CHARS = 1024 * 1024
MAX_QUEUED_LINES = 1000
_OVERSIZED = "\x00oversized"


class ScanAborted(RuntimeError):
    """The scan stopped early: CI needs the host, disk ran low, cancel, or timeout."""


def kill_tree(proc: subprocess.Popen[str]) -> None:
    """Kill ``proc`` and every process it started (it leads its own group)."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(  # noqa: S603
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],  # noqa: S607
                capture_output=True,
                timeout=30,
                check=False,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        proc.kill()
    proc.wait(timeout=30)


def _pump(proc: subprocess.Popen[str], put: Callable[[str | None], None]) -> None:
    """Forward ``proc``'s lines; an over-long line becomes one ``_OVERSIZED`` marker."""
    assert proc.stdout is not None  # noqa: S101
    try:
        while line := proc.stdout.readline(MAX_LINE_CHARS):
            if len(line) >= MAX_LINE_CHARS and not line.endswith("\n"):
                while line and not line.endswith("\n"):
                    line = proc.stdout.readline(MAX_LINE_CHARS)
                line = _OVERSIZED
            put(line)
    except (OSError, ValueError):
        pass  # the pipe closed under us: the process was killed
    put(None)


def watch_process(
    popen: Callable[..., subprocess.Popen[str]],
    argv: list[str],
    env: Mapping[str, str],
    timeout_s: float,
    *,
    idle_s: float,
    on_line: Callable[[str], None] | None,
    stdin_text: str | None,
    cwd: Path | None,
    cancelled: threading.Event,
    poll_s: float,
    poll: Callable[[], None],
    kill: Callable[[subprocess.Popen[str]], None],
) -> None:
    """Run ``argv`` to completion; raise :class:`ScanAborted` on any stop condition.

    ``poll`` runs every ``poll_s`` seconds and raises to stop the run (CI queued,
    disk low). With ``on_line`` the stream is the agent's, so an oversized line
    aborts; without it (a build) such a line is dropped and a non-zero exit fails.
    """
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = popen(
        argv,
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=dict(env),
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        **kwargs,
    )
    lines: queue.Queue[str | None] = queue.Queue(maxsize=MAX_QUEUED_LINES)
    stopped = threading.Event()

    def put(item: str | None) -> None:
        while not stopped.is_set():
            try:
                lines.put(item, timeout=1.0)
                return
            except queue.Full:
                continue

    threading.Thread(target=_pump, args=(proc, put), daemon=True).start()
    if stdin_text is not None and proc.stdin is not None:
        proc.stdin.write(stdin_text)
        proc.stdin.close()
    start = last_line = last_poll = time.monotonic()
    try:
        while True:
            try:
                line = lines.get(timeout=1.0)
            except queue.Empty:
                line = ""
            now = time.monotonic()
            if line is None:
                break
            if line:
                last_line = now
                if line == _OVERSIZED and on_line is not None:
                    raise ScanAborted(
                        f"{argv[0]} wrote a line over {MAX_LINE_CHARS} chars"
                    )
                if on_line is not None:
                    on_line(line)
            if cancelled.is_set():
                raise ScanAborted("cancelled by operator")
            if now - start > timeout_s:
                raise ScanAborted(f"{argv[0]} exceeded {int(timeout_s)} s")
            if now - last_line > idle_s:
                raise ScanAborted(f"{argv[0]} silent for {int(idle_s)} s")
            if now - last_poll >= poll_s:
                last_poll = now
                poll()
        code = proc.wait(timeout=60)
        if code != 0 and on_line is None:
            raise RuntimeError(f"{Path(argv[0]).name} {argv[1]} exited {code}")
    finally:
        stopped.set()
        kill(proc)
