"""Server-side persistence for staff role pins (SC-D3, Issue #1317).

Provides per-user role pinning stored in SQLite (`staff_pins` table in `staff_runs.sqlite3`).
Ensures pins persist across browsers and machines.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from staff.audit import record_audit

log = logging.getLogger("dashboard.staff.pins")

_PINS_STORE: StaffPinsStore | None = None
_STORE_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _config_dir() -> Path:
    base = os.environ.get("RUNNER_DASHBOARD_CONFIG_DIR")
    if base:
        return Path(base).expanduser()
    return Path.home() / ".config" / "runner-dashboard"


def default_pins_db_path() -> Path:
    return Path(os.environ.get("STAFF_RUNS_DB", str(_config_dir() / "staff_runs.sqlite3"))).expanduser()


class StaffPinsStore:
    """Thread-safe SQLite store for per-user staff role pins."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.path = db_path or default_pins_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS staff_pins (
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, role)
                );
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS staff_pins_user_idx ON staff_pins(user_id);")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get_pins(self, user_id: str) -> list[str]:
        """Return the list of pinned role names for a user, ordered by creation time."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT role FROM staff_pins WHERE user_id = ? ORDER BY created_at ASC",
                (user_id,),
            ).fetchall()
            return [str(r["role"]) for r in rows]

    def pin_role(self, user_id: str, role: str) -> list[str]:
        """Pin a role for a user and return the updated pins list."""
        clean_role = role.strip()
        if not clean_role:
            return self.get_pins(user_id)
        now = _now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO staff_pins (user_id, role, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, role) DO NOTHING
                """,
                (user_id, clean_role, now),
            )
        record_audit(
            action="staff_pin",
            target=clean_role,
            principal=user_id,
            surface="staff_roster",
            outcome="success",
            fail_closed=False,
        )
        return self.get_pins(user_id)

    def unpin_role(self, user_id: str, role: str) -> list[str]:
        """Unpin a role for a user and return the updated pins list."""
        clean_role = role.strip()
        with self._lock:
            self._conn.execute(
                "DELETE FROM staff_pins WHERE user_id = ? AND role = ?",
                (user_id, clean_role),
            )
        record_audit(
            action="staff_unpin",
            target=clean_role,
            principal=user_id,
            surface="staff_roster",
            outcome="success",
            fail_closed=False,
        )
        return self.get_pins(user_id)

    def set_pins(self, user_id: str, roles: list[str]) -> list[str]:
        """Replace all pins for a user with the provided roles."""
        now = _now()
        with self._lock:
            self._conn.execute("DELETE FROM staff_pins WHERE user_id = ?", (user_id,))
            for r in roles:
                clean_r = r.strip()
                if clean_r:
                    self._conn.execute(
                        """
                        INSERT INTO staff_pins (user_id, role, created_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(user_id, role) DO NOTHING
                        """,
                        (user_id, clean_r, now),
                    )
        record_audit(
            action="staff_pins_replace",
            target=f"{len(roles)} roles",
            principal=user_id,
            surface="staff_roster",
            outcome="success",
            fail_closed=False,
        )
        return self.get_pins(user_id)


def get_pins_store() -> StaffPinsStore:
    global _PINS_STORE
    with _STORE_LOCK:
        if _PINS_STORE is None:
            _PINS_STORE = StaffPinsStore()
        return _PINS_STORE


def reset_pins_store() -> None:
    global _PINS_STORE
    with _STORE_LOCK:
        if _PINS_STORE is not None:
            try:
                _PINS_STORE.close()
            except Exception:
                pass
            _PINS_STORE = None
