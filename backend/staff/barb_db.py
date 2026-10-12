"""Tiny SQLite transaction helper shared by the Barb authority modules (#1800, BR-06)."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager


class BarbConfigError(ValueError):
    """A ``BARB_*`` environment value is malformed (names the variable and the bad value)."""


def env_positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    """A positive int from ``env[name]`` (blank or absent = ``default``); else :class:`BarbConfigError`."""
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise BarbConfigError(f"{name}={raw!r} is not an integer") from None
    if value < 1:
        raise BarbConfigError(f"{name}={raw!r} must be >= 1")
    return value


@contextmanager
def immediate_txn(conn: sqlite3.Connection, lock: threading.RLock) -> Iterator[sqlite3.Connection]:
    """Run the body in one ``BEGIN IMMEDIATE`` transaction, or join the caller's open one.

    Post: the body's writes commit together or, on any exception (including a failed COMMIT), not at all
    and the connection is left outside a transaction.
    The connection is expected to be in autocommit mode (``isolation_level=None``).
    """
    with lock:
        if conn.in_transaction:
            yield conn
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            # A failed COMMIT (disk full, I/O error) must not leave the transaction open: a later
            # call would otherwise "join" it and never commit, silently breaking RPO = 0.
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
