"""Per-thread working directory for staff chat turns (#1655).

Split out of ``staff.chat`` to keep that module under the 500-line cap.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import tempfile
from pathlib import Path

log = logging.getLogger("dashboard.staff.chat")

__all__ = ["thread_scratch_dir"]


def thread_scratch_dir(thread_id: str) -> str:
    """The read-only working directory every chat turn of ``thread_id`` runs in (#1655).

    The claude CLI files a session under a project directory keyed by the working
    directory, so ``--resume`` only finds turn 1 when turn 2 runs in the same
    directory. The directory is stable per thread, distinct between threads, and kept
    between turns (turns of one thread may overlap, so no turn may remove it).

    Pre: ``thread_id`` is non-empty.
    Post: the returned directory exists, is not a symlink and (on POSIX) is owned by
    this process's user. If the predictable path was planted by someone else, the turn
    runs in a private ``mkdtemp`` directory instead and only loses session resume.
    """
    assert thread_id.strip(), "thread_id must be non-empty"  # noqa: S101
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
    return str(path)
