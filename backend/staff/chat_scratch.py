"""Per-thread working directory for staff chat turns (#1655, #1688).

Split out of ``staff.chat`` to keep that module under the 500-line cap.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

from staff import cli_projects

log = logging.getLogger("dashboard.staff.chat")

__all__ = [
    "SWEEP_INTERVAL_SECONDS",
    "maybe_sweep_idle_chat_scratch",
    "thread_scratch_dir",
]

_last_sweep: float = 0.0
SWEEP_INTERVAL_SECONDS = 3600


def maybe_sweep_idle_chat_scratch(
    env: Mapping[str, str] | None = None,
    *,
    now: float | None = None,
) -> list[str]:
    """Sweep idle chat scratch directories and Claude CLI projects if interval elapsed (#1688).

    Throttled to at most once per SWEEP_INTERVAL_SECONDS (default 1 hour).
    Never raises: catches Exception, logs a warning, and returns [].
    """
    global _last_sweep
    current_time = time.time() if now is None else now
    if current_time - _last_sweep < SWEEP_INTERVAL_SECONDS:
        return []
    _last_sweep = current_time
    try:
        environ = dict(env if env is not None else os.environ)
        return cli_projects.sweep_idle_chat_scratch(environ, now=current_time)
    except Exception as exc:  # noqa: BLE001
        log.warning("failed to sweep idle chat scratch directories: %s", exc)
        return []


def thread_scratch_dir(thread_id: str) -> str:
    """The read-only working directory every chat turn of ``thread_id`` runs in (#1655, #1688).

    The claude CLI files a session under a project directory keyed by the working
    directory, so ``--resume`` only finds turn 1 when turn 2 runs in the same
    directory. The directory is stable per thread, distinct between threads, and kept
    between turns (turns of one thread may overlap, so no turn may remove it).

    Before acquiring or creating the directory, a background sweep cleans up idle chat
    scratch directories older than the idle threshold. The sweep never removes the
    directory of the thread being opened: after the sweep runs, this function creates
    and validates the thread path as normal; if the thread directory was just swept as
    idle, it simply starts a fresh session.

    Pre: ``thread_id`` is non-empty.
    Post: the returned directory exists, is not a symlink and (on POSIX) is owned by
    this process's user. If the predictable path was planted by someone else, the turn
    runs in a private ``mkdtemp`` directory instead and only loses session resume.
    """
    assert thread_id.strip(), "thread_id must be non-empty"  # noqa: S101
    try:
        maybe_sweep_idle_chat_scratch()
    except Exception as exc:  # noqa: BLE001
        log.warning("failed to invoke chat scratch sweep: %s", exc)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", thread_id)
    path = Path(tempfile.gettempdir()) / f"staff_chat_{safe}"
    try:
        path.mkdir(mode=0o700, exist_ok=True)
        info = path.lstat()
    except OSError:
        info = None
    owner_ok = info is not None and (not hasattr(os, "getuid") or info.st_uid == os.getuid())
    if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or not owner_ok:
        log.warning("Chat scratch path %s is not a private directory; using a one-turn directory", path)
        return tempfile.mkdtemp(prefix="staff_chat_")
    try:
        os.utime(path)  # mark the thread active so the idle sweep skips it (#1688)
    except OSError:
        pass
    return str(path)
