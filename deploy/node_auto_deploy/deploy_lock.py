"""Liveness metadata and stale reclaim for ``.node-auto-deploy.lock`` (#4277 H-07)."""

from __future__ import annotations

import os
import socket
import time
from collections.abc import Callable
from pathlib import Path

DEFAULT_LOCK_TTL_SECONDS = float(os.environ.get("NODE_AUTO_DEPLOY_LOCK_TTL_SECONDS", "7200"))


def lock_fields(text: str) -> dict[str, str]:
    """Parse ``key=value`` lines; the first occurrence of a key wins."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key not in fields:
            fields[key] = value.strip()
    return fields


def _proc_root(proc_root: Path | None) -> Path:
    return proc_root if proc_root is not None else Path(os.environ.get("PROC_ROOT", "/proc"))


def _pid_alive(pid: int, proc_root: Path | None) -> bool:
    if pid <= 0:
        return False
    root = _proc_root(proc_root)
    if not root.is_dir():
        return False
    return (root / str(pid)).is_dir()


def _lock_age_seconds(lock_path: Path, clock: Callable[[], float]) -> float | None:
    try:
        return max(0.0, clock() - lock_path.stat().st_mtime)
    except OSError:
        return None


def format_lock_body(*, pid: int, started_at: float, host: str) -> str:
    return f"pid={pid}\nstarted_at={started_at:.6f}\nhost={host}\n"


def lock_is_stale(
    text: str,
    *,
    lock_path: Path | None = None,
    host: str | None = None,
    proc_root: Path | None = None,
    clock: Callable[[], float] = time.time,
    ttl_seconds: float | None = None,
) -> bool:
    """True when the recorded owner is gone or the lock has outlived ``ttl_seconds``."""
    ttl = DEFAULT_LOCK_TTL_SECONDS if ttl_seconds is None else float(ttl_seconds)
    assert ttl > 0  # noqa: S101 - contract
    fields = lock_fields(text)
    recorded_host = fields.get("host", "")
    if host is not None and recorded_host and recorded_host != host:
        return False
    pid_value = fields.get("pid", "")
    pid = int(pid_value) if pid_value.isdigit() else None
    started_value = fields.get("started_at", "")
    started_at = float(started_value) if started_value else None
    now = clock()
    if started_at is not None and now - started_at >= ttl:
        return True
    if pid is not None:
        if not _pid_alive(pid, proc_root):
            return True
        if started_at is None and lock_path is not None:
            age = _lock_age_seconds(lock_path, clock)
            if age is not None and age >= ttl:
                return True
        return False
    if lock_path is None:
        return True
    age = _lock_age_seconds(lock_path, clock)
    return age is not None and age >= ttl


def write_lock_metadata(
    lock_path: Path,
    *,
    proc_root: Path | None = None,
    clock: Callable[[], float] = time.time,
    host: str | None = None,
) -> str:
    """Replace lock contents with pid, start time, and host; return the body written."""
    body = format_lock_body(
        pid=os.getpid(),
        started_at=clock(),
        host=host or socket.gethostname(),
    )
    lock_path.write_text(body, encoding="ascii")
    return body


def try_reclaim_stale_lock(
    lock_path: Path,
    *,
    host: str | None = None,
    proc_root: Path | None = None,
    clock: Callable[[], float] = time.time,
    ttl_seconds: float | None = None,
) -> bool:
    """Remove ``lock_path`` when its metadata proves the owner is stale."""
    try:
        text = lock_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if not lock_is_stale(
        text,
        lock_path=lock_path,
        host=host,
        proc_root=proc_root,
        clock=clock,
        ttl_seconds=ttl_seconds,
    ):
        return False
    try:
        lock_path.unlink()
    except OSError:
        return False
    return True
