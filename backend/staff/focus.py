"""Fleet focus for staff prompts: board priorities and operator directives (issue #1239).

A staff run should work on what the board and the operator asked for. This module turns
``priorities.service.top_priorities`` into one prompt paragraph scoped to the run's repo.
The priorities module is optional at runtime (older nodes, missing RM checkout): any
failure yields an empty paragraph, never a failed run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger("dashboard.staff.focus")

MAX_ITEMS = 5
FETCH_LIMIT = 50


def _default_loader() -> list[dict[str, Any]]:
    from priorities.service import top_priorities  # noqa: PLC0415 - optional module, imported lazily

    return top_priorities(FETCH_LIMIT)


def load_items(loader: Callable[[], list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
    """Board + directive items, or ``[]`` when priorities are unavailable on this node."""
    try:
        items = (loader or _default_loader)()
    except Exception as exc:  # noqa: BLE001 - focus is advisory; a run must never fail on it
        log.info("staff focus unavailable: %s", exc)
        return []
    return [i for i in items if isinstance(i, dict)]


def _applies(item: dict[str, Any], repo: str) -> bool:
    wanted = repo.casefold()
    if item.get("kind") == "directive":
        scope = str(item.get("repo") or "*").casefold()
        return scope == "*" or scope == wanted
    project = str(item.get("project") or "").casefold()
    return bool(wanted) and wanted in project


def _line(item: dict[str, Any]) -> str:
    if item.get("kind") == "directive":
        return f"- [directive p{item.get('priority', '?')}] {item.get('text', '').strip()}"
    parts = [f"- [board #{item.get('rank', '?')}] {str(item.get('item', '')).strip()}"]
    if item.get("tracking"):
        parts.append(f"(tracking {item['tracking']})")
    if item.get("acceptance"):
        parts.append(f"— done when: {str(item['acceptance']).strip()}")
    return " ".join(parts)


def focus_paragraph(repo: str, items: list[dict[str, Any]]) -> str:
    """Prompt paragraph with the items that apply to ``repo`` (all-repo directives included).

    Post: empty string when nothing applies; otherwise at most ``MAX_ITEMS`` bullet lines.
    """
    relevant = [i for i in items if _applies(i, repo)][:MAX_ITEMS]
    if not relevant:
        return ""
    lines = "\n".join(_line(i) for i in relevant)
    paragraph = (
        "Fleet focus (latest board meeting and operator directives). When you choose between items, "
        "prefer work that advances these; do not start work that contradicts a directive:\n" + lines
    )
    assert paragraph.count("\n- [") <= MAX_ITEMS  # noqa: S101 - postcondition
    return paragraph
