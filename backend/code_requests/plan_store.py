"""Node-local persistence for planning sessions (CR-4, #1285).

Drafts live here until approval; once filed, GitHub (the plan epic and its children)
is the durable record and this file only remembers progress and the last ingested
comment.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from code_requests.planner import PlanningSession
from config_schema import atomic_write_json

DEFAULT_PLAN_SESSIONS_PATH = Path.home() / "actions-runners" / "dashboard" / "code_request_plans.json"


class PlanSessionStore:
    """JSON-file store of ``PlanningSession`` keyed by Code Request id."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_PLAN_SESSIONS_PATH
        self._lock = threading.Lock()

    def _read(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, request_id: str) -> PlanningSession | None:
        with self._lock:
            raw = self._read().get(request_id)
        return PlanningSession.model_validate(raw) if isinstance(raw, dict) else None

    def save(self, session: PlanningSession) -> PlanningSession:
        with self._lock:
            data = self._read()
            data[session.request_id] = session.model_dump(mode="json")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.path, data)
        return session
