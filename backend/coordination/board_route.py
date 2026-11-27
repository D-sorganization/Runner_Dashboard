"""RM board route. Callers fail closed when config does not name a board (#4686)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_BOARD_REPO = "D-sorganization/Repository_Management"
_MISSING_BOARD_CONFIG = "RM config/agent_communication.json is absent"


class BoardConfigUnavailable(RuntimeError):
    """The RM checkout has no board route. Callers must fail closed, not guess an old issue."""


def load_board_config(root: Path | None) -> tuple[str, int, int, list[int]]:
    """Return ``(repo, issue, window_days, predecessors)`` from ``root``.

    Pre: ``root/config/agent_communication.json`` exists and names ``board_issue``.
    Post: raises :class:`BoardConfigUnavailable` when the file or issue is absent.
    A present file that is not a JSON object raises ``RuntimeError``.
    """
    cfg = (root / "config" / "agent_communication.json") if root else None
    if cfg is None or not cfg.is_file():
        raise BoardConfigUnavailable(_MISSING_BOARD_CONFIG)
    try:
        parsed = json.loads(cfg.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("root must be a dict")
        if parsed.get("board_issue") in (None, ""):
            raise BoardConfigUnavailable("board_issue is absent")
        repo = str(parsed.get("board_repo") or _BOARD_REPO)
        issue = int(parsed["board_issue"])
        days = int(parsed.get("board_window_days") or 3)
        preds = [int(p) for p in parsed.get("board_predecessors") or []]
    except BoardConfigUnavailable:
        raise
    except Exception as exc:
        raise RuntimeError(f"Invalid agent_communication.json: {exc}") from exc
    return repo, issue, days, preds


def unavailable_board_read(reason: str) -> dict[str, Any]:
    """One fail-closed board read. Post: no session is treated as live."""
    return {
        "available": False,
        "reason": reason,
        "complete": False,
        "pages_complete": False,
        "degraded_history": False,
        "read_warnings": [reason],
        "replay_warnings": [],
        "active_conflicts": [],
        "comment_limit_exceeded": False,
        "comment_count": 0,
        "sessions": [],
        "messages": [],
        "conflicts": [],
        "warnings": [reason],
    }
