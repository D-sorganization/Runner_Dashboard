"""Tracked fleet projects: intake, decomposition, routing hints (#2157 slice 5)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from staff.store import _config_dir, _now


def default_db_path() -> Path:
    return Path(os.environ.get("STAFF_PROJECTS_DB", str(_config_dir() / "staff_projects.sqlite3"))).expanduser()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS staff_projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    definition_of_done TEXT NOT NULL DEFAULT '',
    repos TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    tasks_json TEXT NOT NULL DEFAULT '[]'
);
"""


@dataclass
class ProjectTask:
    id: str
    title: str
    role: str
    repo: str
    task_type: str
    issue_hint: str = ""


@dataclass
class ProjectRecord:
    id: str
    title: str
    goal: str
    definition_of_done: str
    repos: list[str]
    status: str
    created_by: str
    created_at: str
    tasks: list[ProjectTask] = field(default_factory=list)


class ProjectIntakeStore:
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

    def create(
        self,
        *,
        title: str,
        goal: str,
        definition_of_done: str = "",
        repos: list[str] | None = None,
        created_by: str = "",
    ) -> ProjectRecord:
        title = title.strip()
        goal = goal.strip()
        if not title or not goal:
            raise ValueError("title and goal are required")
        rec = ProjectRecord(
            id=f"prj_{uuid.uuid4().hex[:12]}",
            title=title,
            goal=goal,
            definition_of_done=definition_of_done.strip(),
            repos=[r.strip() for r in (repos or []) if r.strip()],
            status="active",
            created_by=created_by,
            created_at=_now(),
            tasks=[],
        )
        with self._lock:
            self._connection().execute(
                "INSERT INTO staff_projects "
                "(id, title, goal, definition_of_done, repos, status, created_by, created_at, tasks_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rec.id,
                    rec.title,
                    rec.goal,
                    rec.definition_of_done,
                    json.dumps(rec.repos),
                    rec.status,
                    rec.created_by,
                    rec.created_at,
                    "[]",
                ),
            )
        return rec

    def get(self, project_id: str) -> ProjectRecord | None:
        with self._lock:
            row = self._connection().execute("SELECT * FROM staff_projects WHERE id = ?", (project_id,)).fetchone()
        return _row_to_record(row) if row else None

    def list_projects(self, limit: int = 50) -> list[ProjectRecord]:
        with self._lock:
            rows = (
                self._connection()
                .execute("SELECT * FROM staff_projects ORDER BY created_at DESC LIMIT ?", (limit,))
                .fetchall()
            )
        return [_row_to_record(r) for r in rows]

    def save_tasks(self, project_id: str, tasks: list[ProjectTask]) -> ProjectRecord:
        payload = json.dumps([task.__dict__ for task in tasks])
        with self._lock:
            self._connection().execute(
                "UPDATE staff_projects SET tasks_json = ? WHERE id = ?",
                (payload, project_id),
            )
        rec = self.get(project_id)
        if rec is None:
            raise ValueError("project not found")
        return rec


def _row_to_record(row: sqlite3.Row) -> ProjectRecord:
    repos = json.loads(row["repos"] or "[]")
    tasks_raw = json.loads(row["tasks_json"] or "[]")
    tasks = [ProjectTask(**t) for t in tasks_raw if isinstance(t, dict)]
    return ProjectRecord(
        id=row["id"],
        title=row["title"],
        goal=row["goal"],
        definition_of_done=row["definition_of_done"],
        repos=repos if isinstance(repos, list) else [],
        status=row["status"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        tasks=tasks,
    )


_store: ProjectIntakeStore | None = None
_store_lock = threading.Lock()


def get_project_store() -> ProjectIntakeStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = ProjectIntakeStore()
        return _store


def decompose_project(rec: ProjectRecord) -> list[ProjectTask]:
    """Rule-based slice plan until Barb LLM routing lands."""
    repo = rec.repos[0] if rec.repos else "Runner_Dashboard"
    base = [
        ProjectTask(
            id=f"tsk_{uuid.uuid4().hex[:8]}",
            title="Clarify definition of done",
            role="barb",
            repo=repo,
            task_type="staff-dispatch",
            issue_hint="",
        ),
        ProjectTask(
            id=f"tsk_{uuid.uuid4().hex[:8]}",
            title=f"Implement: {rec.goal[:120]}",
            role="issue-remediator",
            repo=repo,
            task_type="open-pr",
            issue_hint="",
        ),
        ProjectTask(
            id=f"tsk_{uuid.uuid4().hex[:8]}",
            title="Verify with meaningful tests",
            role="test-hygiene",
            repo=repo,
            task_type="write-tests",
            issue_hint="",
        ),
    ]
    return base


def project_to_dict(rec: ProjectRecord) -> dict[str, Any]:
    return {
        "id": rec.id,
        "title": rec.title,
        "goal": rec.goal,
        "definition_of_done": rec.definition_of_done,
        "repos": rec.repos,
        "status": rec.status,
        "created_by": rec.created_by,
        "created_at": rec.created_at,
        "tasks": [t.__dict__ for t in rec.tasks],
    }
