"""Ensure ``_cpu_history`` is a bounded ``collections.deque`` (#393).

An unbounded list would grow forever in a long-lived process; capping the
deque guarantees memory stays flat regardless of uptime.
"""

from __future__ import annotations

import collections

import server


def test_cpu_history_is_a_deque() -> None:
    assert isinstance(server._cpu_history, collections.deque)


def test_cpu_history_has_maxlen() -> None:
    assert server._cpu_history.maxlen is not None
    assert server._cpu_history.maxlen > 0


def test_cpu_history_maxlen_constant_is_exposed() -> None:
    """The maxlen value should come from a named module-level constant."""
    assert hasattr(server, "_CPU_HISTORY_MAXLEN")
    assert isinstance(server._CPU_HISTORY_MAXLEN, int)
    assert server._CPU_HISTORY_MAXLEN >= 60
    assert server._cpu_history.maxlen == server._CPU_HISTORY_MAXLEN


def test_cpu_history_truncates_on_overflow() -> None:
    """Pushing more than maxlen elements drops the oldest, never grows."""
    maxlen = server._cpu_history.maxlen
    assert maxlen is not None
    sample = collections.deque(server._cpu_history)  # snapshot to restore later
    try:
        server._cpu_history.clear()
        for i in range(maxlen + 50):
            server._cpu_history.append(float(i))
        assert len(server._cpu_history) == maxlen
        # The oldest 50 entries must have been evicted.
        assert server._cpu_history[0] == 50.0
    finally:
        server._cpu_history.clear()
        server._cpu_history.extend(sample)
