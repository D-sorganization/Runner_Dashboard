"""Tiny SQLite transaction helper shared by the Barb authority modules (#1800, BR-06)."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager


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
