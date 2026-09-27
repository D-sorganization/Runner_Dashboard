"""Node-local persistence for planning sessions (CR-4, #1285).

Drafts live here until approval; once filed, GitHub (the plan epic and its children)
is the durable record and this file only remembers progress and the last ingested
comment.
"""

from __future__ import annotations

from pathlib import Path

from code_requests.keyed_json_store import KeyedJsonStore
from code_requests.planner import PlanningSession

DEFAULT_PLAN_SESSIONS_PATH = Path.home() / "actions-runners" / "dashboard" / "code_request_plans.json"


class PlanSessionStore(KeyedJsonStore[PlanningSession]):
    """JSON-file store of ``PlanningSession`` keyed by Code Request id."""

    def __init__(self, path: Path | None = None) -> None:
        super().__init__(path or DEFAULT_PLAN_SESSIONS_PATH, PlanningSession, lambda s: s.request_id)
