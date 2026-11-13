"""Per-operator personal presentation preferences (#3495): display label, role
order and avatar overrides, keyed by authenticated operator id and immutable
role id. Separate domain from #3491 manual/playbooks; never touches role
identity, scopes, holds, instructions, routing, schedule or activation.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from staff.store import _config_dir, _now

MAX_DISPLAY_LABEL_CHARS = 80
MAX_ROLE_IDS = 200


def default_db_path() -> Path:
    default = str(_config_dir() / "staff_presentation_prefs.sqlite3")
    return Path(os.environ.get("STAFF_PRESENTATION_PREFS_DB", default)).expanduser()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS staff_presentation_prefs (
    operator_id TEXT PRIMARY KEY,
    display_label TEXT NOT NULL DEFAULT '',
    role_order_json TEXT NOT NULL DEFAULT '[]',
    avatar_overrides_json TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""


class PreferencesConflictError(Exception):
    """Raised when the caller's expected revision no longer matches the stored one."""

    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__(f"stale revision; current revision is {current_revision}")


class PreferencesValidationError(ValueError):
    """Raised for any bounded-input or known-role-id violation."""


@dataclass
class PresentationPreferences:
    """Flat, operator-scoped preference record (LoD: no nested role objects)."""

    operator_id: str
    display_label: str = ""
    role_order: list[str] = field(default_factory=list)
    avatar_overrides: dict[str, str] = field(default_factory=dict)
    revision: int = 0
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "operator_id": self.operator_id,
            "display_label": self.display_label,
            "role_order": list(self.role_order),
            "avatar_overrides": dict(self.avatar_overrides),
            "revision": self.revision,
            "updated_at": self.updated_at,
        }


def _defaults(operator_id: str) -> PresentationPreferences:
    """Absence of a stored record is not an error (#3495): return defaults."""
    return PresentationPreferences(operator_id=operator_id)


class PresentationPrefsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_db_path()
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=30.0)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
        return self._conn

    def get(self, operator_id: str) -> PresentationPreferences:
        with self._lock:
            row = (
                self._connection()
                .execute("SELECT * FROM staff_presentation_prefs WHERE operator_id = ?", (operator_id,))
                .fetchone()
            )
        if row is None:
            return _defaults(operator_id)
        return PresentationPreferences(
            operator_id=row["operator_id"],
            display_label=row["display_label"],
            role_order=json.loads(row["role_order_json"]),
            avatar_overrides=json.loads(row["avatar_overrides_json"]),
            revision=row["revision"],
            updated_at=row["updated_at"],
        )

    def save(
        self,
        operator_id: str,
        *,
        expected_revision: int,
        display_label: str,
        role_order: list[str],
        avatar_overrides: dict[str, str],
        known_role_ids: frozenset[str],
    ) -> PresentationPreferences:
        """Validate and persist; raises on any boundary violation, fails closed.

        ``expected_revision`` must equal 0 for a first write, or the currently
        stored revision for an update — a mismatch raises
        ``PreferencesConflictError`` rather than silently overwriting a
        concurrent change.
        """
        _validate_display_label(display_label)
        _validate_role_order(role_order, known_role_ids)
        _validate_avatar_overrides(avatar_overrides, known_role_ids)

        with self._lock:
            conn = self._connection()
            row = conn.execute(
                "SELECT revision FROM staff_presentation_prefs WHERE operator_id = ?", (operator_id,)
            ).fetchone()
            current_revision = row["revision"] if row is not None else 0
            if expected_revision != current_revision:
                raise PreferencesConflictError(current_revision)

            next_revision = current_revision + 1
            updated_at = _now()
            conn.execute(
                """
                INSERT INTO staff_presentation_prefs
                    (operator_id, display_label, role_order_json, avatar_overrides_json, revision, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(operator_id) DO UPDATE SET
                    display_label = excluded.display_label,
                    role_order_json = excluded.role_order_json,
                    avatar_overrides_json = excluded.avatar_overrides_json,
                    revision = excluded.revision,
                    updated_at = excluded.updated_at
                """,
                (
                    operator_id,
                    display_label,
                    json.dumps(role_order),
                    json.dumps(avatar_overrides),
                    next_revision,
                    updated_at,
                ),
            )
        return PresentationPreferences(
            operator_id=operator_id,
            display_label=display_label,
            role_order=list(role_order),
            avatar_overrides=dict(avatar_overrides),
            revision=next_revision,
            updated_at=updated_at,
        )

    def reset(self, operator_id: str) -> None:
        """Delete the stored record; next ``get`` returns defaults again."""
        with self._lock:
            self._connection().execute("DELETE FROM staff_presentation_prefs WHERE operator_id = ?", (operator_id,))


def _validate_display_label(display_label: str) -> None:
    if len(display_label) > MAX_DISPLAY_LABEL_CHARS:
        raise PreferencesValidationError(f"display_label exceeds {MAX_DISPLAY_LABEL_CHARS} characters")
    if any(ord(ch) < 0x20 for ch in display_label):
        raise PreferencesValidationError("display_label must be plain text (no control characters)")


def _validate_role_order(role_order: list[str], known_role_ids: frozenset[str]) -> None:
    if len(role_order) > MAX_ROLE_IDS:
        raise PreferencesValidationError(f"role_order exceeds {MAX_ROLE_IDS} entries")
    if len(set(role_order)) != len(role_order):
        raise PreferencesValidationError("role_order contains duplicate role ids")
    unknown = [role_id for role_id in role_order if role_id not in known_role_ids]
    if unknown:
        raise PreferencesValidationError(f"unknown role id(s) in role_order: {unknown!r}")


def _validate_avatar_overrides(avatar_overrides: dict[str, str], known_role_ids: frozenset[str]) -> None:
    unknown = [role_id for role_id in avatar_overrides if role_id not in known_role_ids]
    if unknown:
        raise PreferencesValidationError(f"unknown role id(s) in avatar_overrides: {unknown!r}")


_STORE: PresentationPrefsStore | None = None
_STORE_LOCK = threading.Lock()


def get_presentation_prefs_store() -> PresentationPrefsStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = PresentationPrefsStore()
        return _STORE
