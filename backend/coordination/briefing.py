"""The pre-work briefing: one call an agent makes before starting (issue #1229).

Composes what already exists — priorities (when the sibling ``priorities``
package is installed), active holds, board sessions and staff runs in the
repo, the claim protocol and the fleet rules — without owning any of it.
Every part degrades independently; a failing part adds a warning.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from typing import Any

from coordination import board
from coordination.service import now_iso
from coordination.staff_view import staff_runs
from staff.scheduler import get_scheduler
from staff.workspace import FLEET_RULES

log = logging.getLogger("dashboard.coordination")

PRIORITY_LIMIT = 5
CLAIMS_HINT = (
    "Before editing: GET /api/coordination/claims?repo=&issue= then POST /api/coordination/claims "
    "{repo, issue, agent, session}; 409 means another agent holds it — pick other work. Register presence with "
    "POST /api/coordination/presence and read GET /api/coordination/inbox?session= at each checkpoint."
)
ENDPOINTS = {
    "briefing": "GET /api/coordination/briefing?repo=&agent=",
    "sessions": "GET /api/coordination/sessions?repo=",
    "inbox": "GET /api/coordination/inbox?session=&repo=",
    "presence": "POST /api/coordination/presence",
    "presence_release": "POST /api/coordination/presence/release",
    "messages": "POST /api/coordination/messages",
    "messages_ack": "POST /api/coordination/messages/ack",
    "claims": "GET|POST /api/coordination/claims",
    "claims_release": "POST /api/coordination/claims/release",
    "priorities": "GET /api/priorities",
    "staff": "GET /api/staff/summary",
}


async def _priorities(limit: int) -> tuple[list[Any], list[str]]:
    """``priorities.service.top_priorities(limit)`` when that module exists; ``[]`` otherwise."""
    try:
        module = importlib.import_module("priorities.service")
    except ImportError:
        return [], []
    loader = getattr(module, "top_priorities", None)
    if loader is None:
        return [], []
    try:
        result = loader(limit)
        if inspect.isawaitable(result):
            result = await result
        return list(result or []), []
    except Exception as exc:  # noqa: BLE001 — orthogonality: priorities failure must not break the briefing
        log.warning("coordination: priorities unavailable: %s", exc)
        return [], [f"priorities unavailable: {exc}"]


def _holds() -> tuple[list[dict[str, Any]], list[str]]:
    try:
        return [h.to_dict() for h in get_scheduler().holds.load() if h.active], []
    except Exception as exc:  # noqa: BLE001
        log.warning("coordination: holds unavailable: %s", exc)
        return [], [f"holds unavailable: {exc}"]


async def build_briefing(repo: str, agent: str | None) -> dict[str, Any]:
    priorities, w_priorities = await _priorities(PRIORITY_LIMIT)
    holds, w_holds = _holds()
    board_view = await asyncio.to_thread(board.read_sessions, repo)
    runs, w_runs = await staff_runs(repo)
    board_warnings = list(board_view.get("warnings", []))
    if not board_view["available"]:
        board_warnings.append(f"board unavailable: {board_view.get('reason', '')}")
    return {
        "generated_at": now_iso(),
        "repo": repo,
        "agent": agent,
        "priorities": priorities,
        "holds": holds,
        "sessions": board_view["sessions"],
        "board_available": board_view["available"],
        "staff_runs": runs,
        "claims_hint": CLAIMS_HINT,
        "rules": [FLEET_RULES],
        "endpoints": ENDPOINTS,
        "warnings": [*w_priorities, *w_holds, *board_warnings, *w_runs],
    }
