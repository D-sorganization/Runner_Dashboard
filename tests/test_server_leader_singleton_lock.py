"""Dashboard server leader-lock semantics (issue #3172).

Before #3172, startup caught every ``OSError`` from the candidate-path loop
(including ``BlockingIOError`` when ``flock(LOCK_NB)`` finds the lock held) and
continued to the next path. A second dashboard instance could then acquire a
*different* lock file and run leader background tasks concurrently.

Mirrors the autoscaler fix in #933: separate open/makedirs (unusable path → try
next) from flock (held lock → follower, do not fall through).
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")


def _import_server():
    import server  # noqa: PLC0415

    return server


def test_held_primary_lock_does_not_fall_through(tmp_path, monkeypatch):
    """A held primary lock must not acquire an alternate path (#3172)."""
    import fcntl

    server = _import_server()
    primary = tmp_path / "primary.lock"
    fallback = tmp_path / "fallback.lock"

    holder = open(primary, "w")  # noqa: SIM115 — retain lock until test ends
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    monkeypatch.setattr(
        server,
        "_leader_lock_candidate_paths",
        lambda: [str(primary), str(fallback)],
    )
    monkeypatch.setattr(server, "_leader_lock_fd", None, raising=False)

    try:
        assert server._acquire_leader_lock() is False
        assert server._leader_lock_fd is None
        assert not fallback.exists()
    finally:
        try:
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        finally:
            holder.close()


def test_free_lock_is_acquired(tmp_path, monkeypatch):
    """When the primary candidate is free, the lock is acquired (no regression)."""
    server = _import_server()
    primary = tmp_path / "free.lock"
    monkeypatch.setattr(server, "_leader_lock_candidate_paths", lambda: [str(primary)])
    monkeypatch.setattr(server, "_leader_lock_fd", None, raising=False)

    try:
        assert server._acquire_leader_lock() is True
        assert server._leader_lock_fd is not None
        assert primary.exists()
    finally:
        if server._leader_lock_fd is not None:
            server._leader_lock_fd.close()
            server._leader_lock_fd = None


def test_unwritable_primary_falls_back_to_next(tmp_path, monkeypatch):
    """A genuinely unusable primary path must fall through to a writable candidate."""
    server = _import_server()
    not_a_dir = tmp_path / "iamfile"
    not_a_dir.write_text("x")
    unusable = not_a_dir / "child" / "lock"
    fallback = tmp_path / "fallback.lock"

    monkeypatch.setattr(
        server,
        "_leader_lock_candidate_paths",
        lambda: [str(unusable), str(fallback)],
    )
    monkeypatch.setattr(server, "_leader_lock_fd", None, raising=False)

    try:
        assert server._acquire_leader_lock() is True
        assert server._leader_lock_fd is not None
        assert fallback.exists()
    finally:
        if server._leader_lock_fd is not None:
            server._leader_lock_fd.close()
            server._leader_lock_fd = None


def test_held_lock_logs_distinct_from_unusable(tmp_path, monkeypatch, caplog):
    """Held vs unusable paths must be distinguishable in logs (#3172)."""
    import fcntl

    server = _import_server()
    primary = tmp_path / "held.lock"
    holder = open(primary, "w")  # noqa: SIM115
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(server, "_leader_lock_candidate_paths", lambda: [str(primary)])
    monkeypatch.setattr(server, "_leader_lock_fd", None, raising=False)

    try:
        with caplog.at_level(logging.INFO, logger=server.log.name):
            assert server._acquire_leader_lock() is False
        assert "held" in caplog.text.lower()
        assert "alternate" in caplog.text.lower() or "not trying" in caplog.text.lower()
    finally:
        try:
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        finally:
            holder.close()

    caplog.clear()
    not_a_dir = tmp_path / "blocker"
    not_a_dir.write_text("x")
    unusable = not_a_dir / "nested" / "lock"
    fallback = tmp_path / "after-fallback.lock"
    monkeypatch.setattr(
        server,
        "_leader_lock_candidate_paths",
        lambda: [str(unusable), str(fallback)],
    )
    monkeypatch.setattr(server, "_leader_lock_fd", None, raising=False)

    with caplog.at_level(logging.INFO, logger=server.log.name):
        assert server._acquire_leader_lock() is True
    assert "unusable" in caplog.text.lower() or "next candidate" in caplog.text.lower()


def _subprocess_leader_attempt(lock_path: str, result_path: str) -> None:
    import fcntl

    import server as srv  # noqa: PLC0415

    srv._leader_lock_fd = None
    srv._leader_lock_candidate_paths = lambda: [lock_path]  # type: ignore[method-assign]
    ok = srv._acquire_leader_lock()
    Path(result_path).write_text("1" if ok else "0")


def test_second_process_cannot_become_leader_while_first_holds(tmp_path):
    """Regression: two processes must not both acquire leader status (#3172)."""
    import fcntl

    lock_path = tmp_path / "shared.lock"
    result_a = tmp_path / "a.txt"
    result_b = tmp_path / "b.txt"

    holder = open(lock_path, "w")  # noqa: SIM115
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    ctx = mp.get_context("spawn")
    proc = ctx.Process(
        target=_subprocess_leader_attempt,
        args=(str(lock_path), str(result_b)),
    )
    proc.start()
    proc.join(timeout=30)
    assert proc.exitcode == 0

    assert result_b.read_text() == "0"

    fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
    holder.close()

    proc2 = ctx.Process(
        target=_subprocess_leader_attempt,
        args=(str(lock_path), str(result_a)),
    )
    proc2.start()
    proc2.join(timeout=30)
    assert proc2.exitcode == 0
    assert result_a.read_text() == "1"
