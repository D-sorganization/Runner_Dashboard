"""SQLite store for the fleet friction ledger and the owner-questions queue.

Mirrors the shell orchestrator's FRICTION_LEDGER.md / OWNER_QUESTIONS.md so the
dashboard can monitor and improve fleet workflows over time. One file under the
dashboard config dir (``FLEET_FRICTION_DB`` overrides); every call opens a short
connection, so tests only need to point the env var at a temp path.

Rule enforced here (not only in the route): a friction row can be CLOSED only
with both a non-empty ``fix_ref`` (merged/deployed fix) and a non-empty
``detector`` (a check that makes a recurrence visible).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FRICTION_STATUSES = ("open", "in_progress", "waiting", "closed")
QUESTION_STATUSES = ("open", "answered", "withdrawn")
_FRICTION_UPDATABLE = frozenset(
    {"title", "seen", "repo", "fix", "fix_ref", "detector", "detector_ref", "status"},
)
_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS friction (
    num INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, seen TEXT NOT NULL, repo TEXT,
    fix TEXT NOT NULL DEFAULT '', fix_ref TEXT NOT NULL DEFAULT '',
    detector TEXT NOT NULL DEFAULT '', detector_ref TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open', created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    recurrence_count INTEGER NOT NULL DEFAULT 0, last_seen_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_friction_status ON friction(status);
CREATE TABLE IF NOT EXISTS owner_questions (
    num INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT, issue INTEGER, question TEXT NOT NULL, options TEXT NOT NULL DEFAULT '[]',
    recommended TEXT, asked_by TEXT NOT NULL DEFAULT '', asked_at TEXT NOT NULL,
    answer TEXT, answered_by TEXT, answered_at TEXT, status TEXT NOT NULL DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS idx_owner_questions_status ON owner_questions(status);
"""


class FrictionRuleError(ValueError):
    """A friction update violates the closed-needs-fix-and-detector rule."""


class NotFoundError(LookupError):
    """No row with the requested id."""


class StateError(ValueError):
    """The row is not in a state that allows the requested transition."""


def db_path() -> Path:
    configured = os.environ.get("FLEET_FRICTION_DB")
    if configured:
        return Path(configured).expanduser()
    base = os.environ.get("RUNNER_DASHBOARD_CONFIG_DIR")
    root = Path(base).expanduser() if base else Path("~/.config/runner-dashboard").expanduser()
    return root / "fleet_friction.sqlite3"


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _friction(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["id"] = f"F{d.pop('num')}"
    return d


def _question(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["id"] = f"Q{d.pop('num')}"
    d["options"] = json.loads(d["options"] or "[]")
    return d


def _num(item_id: str, prefix: str) -> int:
    text = item_id.strip().upper()
    if text.startswith(prefix) and text[1:].isdigit():
        return int(text[1:])
    raise NotFoundError(item_id)


def check_closable(status: str, fix_ref: str, detector: str) -> None:
    """Raise unless ``status`` is not closed or both ``fix_ref`` and ``detector`` are non-empty."""
    if status == "closed" and not (fix_ref.strip() and detector.strip()):
        raise FrictionRuleError("status=closed requires a non-empty fix_ref and detector")


# -- friction ---------------------------------------------------------------


def create_friction(
    *,
    title: str,
    seen: str,
    created_by: str,
    repo: str | None = None,
    fix: str = "",
    fix_ref: str = "",
    detector: str = "",
    detector_ref: str = "",
    status: str = "open",
    recurrence_count: int = 0,
    last_seen_at: str | None = None,
) -> dict[str, Any]:
    check_closable(status, fix_ref, detector)
    now = _now()
    with _LOCK, closing(_connect()) as conn:
        cur = conn.execute(
            "INSERT INTO friction (title, seen, repo, fix, fix_ref, detector, detector_ref, status, created_by,"
            " created_at, updated_at, recurrence_count, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                title,
                seen,
                repo,
                fix,
                fix_ref,
                detector,
                detector_ref,
                status,
                created_by,
                now,
                now,
                recurrence_count,
                last_seen_at,
            ),
        )
        conn.commit()
        return get_friction(f"F{cur.lastrowid}", conn=conn)


def get_friction(item_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    num = _num(item_id, "F")
    if conn is None:
        with closing(_connect()) as own:
            return get_friction(item_id, conn=own)
    row = conn.execute("SELECT * FROM friction WHERE num=?", (num,)).fetchone()
    if row is None:
        raise NotFoundError(item_id)
    return _friction(row)


def list_friction(status: str | None = None, repo: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    sql = "SELECT * FROM friction WHERE 1=1"
    args: list[Any] = []
    if status:
        sql += " AND status=?"
        args.append(status)
    if repo:
        sql += " AND repo=?"
        args.append(repo)
    with closing(_connect()) as conn:
        rows = conn.execute(sql + " ORDER BY num DESC LIMIT ?", [*args, limit]).fetchall()
    return [_friction(r) for r in rows]


def update_friction(item_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    num = _num(item_id, "F")
    with _LOCK, closing(_connect()) as conn:
        current = get_friction(item_id, conn=conn)
        cols = [k for k in changes if k in _FRICTION_UPDATABLE]
        merged = {**current, **{c: changes[c] for c in cols}}
        check_closable(merged["status"], merged["fix_ref"], merged["detector"])
        if cols:
            sets = ", ".join(f"{c}=?" for c in cols)
            conn.execute(
                f"UPDATE friction SET {sets}, updated_at=? WHERE num=?",  # noqa: S608 - cols whitelisted above
                [*(merged[c] for c in cols), _now(), num],
            )
            conn.commit()
        return get_friction(item_id, conn=conn)


def record_recurrence(item_id: str) -> dict[str, Any]:
    num = _num(item_id, "F")
    now = _now()
    with _LOCK, closing(_connect()) as conn:
        cur = conn.execute(
            "UPDATE friction SET recurrence_count=recurrence_count+1, last_seen_at=?, updated_at=? WHERE num=?",
            (now, now, num),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise NotFoundError(item_id)
        return get_friction(item_id, conn=conn)


# -- owner questions --------------------------------------------------------


def create_question(
    *,
    question: str,
    asked_by: str,
    repo: str | None = None,
    issue: int | None = None,
    options: list[str] | None = None,
    recommended: str | None = None,
) -> dict[str, Any]:
    with _LOCK, closing(_connect()) as conn:
        cur = conn.execute(
            "INSERT INTO owner_questions (repo, issue, question, options, recommended, asked_by, asked_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (repo, issue, question, json.dumps(options or []), recommended, asked_by, _now()),
        )
        conn.commit()
        return get_question(f"Q{cur.lastrowid}", conn=conn)


def get_question(item_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    num = _num(item_id, "Q")
    if conn is None:
        with closing(_connect()) as own:
            return get_question(item_id, conn=own)
    row = conn.execute("SELECT * FROM owner_questions WHERE num=?", (num,)).fetchone()
    if row is None:
        raise NotFoundError(item_id)
    return _question(row)


def list_questions(status: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    with closing(_connect()) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM owner_questions WHERE status=? ORDER BY num DESC LIMIT ?", (status, limit)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM owner_questions ORDER BY num DESC LIMIT ?", (limit,)).fetchall()
    return [_question(r) for r in rows]


def _transition(item_id: str, status: str, answer: str | None, actor: str) -> dict[str, Any]:
    num = _num(item_id, "Q")
    with _LOCK, closing(_connect()) as conn:
        if get_question(item_id, conn=conn)["status"] != "open":
            raise StateError("question is not open")
        conn.execute(
            "UPDATE owner_questions SET status=?, answer=?, answered_by=?, answered_at=? WHERE num=?",
            (status, answer, actor, _now(), num),
        )
        conn.commit()
        return get_question(item_id, conn=conn)


def answer_question(item_id: str, answer: str, answered_by: str) -> dict[str, Any]:
    return _transition(item_id, "answered", answer, answered_by)


def withdraw_question(item_id: str, actor: str) -> dict[str, Any]:
    return _transition(item_id, "withdrawn", None, actor)


def open_counts() -> dict[str, int]:
    """Counts surfaced in the staff summary; never raises (the summary must keep working)."""
    try:
        with closing(_connect()) as conn:
            fr = conn.execute("SELECT COUNT(*) FROM friction WHERE status <> 'closed'").fetchone()[0]
            oq = conn.execute("SELECT COUNT(*) FROM owner_questions WHERE status = 'open'").fetchone()[0]
        return {"friction_open": int(fr), "owner_questions_open": int(oq)}
    except (sqlite3.Error, OSError):
        return {"friction_open": 0, "owner_questions_open": 0}
