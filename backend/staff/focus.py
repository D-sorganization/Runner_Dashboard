"""Fleet focus for staff prompts: board priorities and operator directives (issue #1239).

A staff run should work on what the board and the operator asked for. This module turns
``priorities.service.top_priorities`` into one prompt paragraph scoped to the run's repo.
The priorities module is optional at runtime (older nodes, missing RM checkout): any
failure yields an empty paragraph, never a failed run. Every item renders as exactly one
line (whitespace, including newlines from a checklist, is collapsed) so a directive can
neither break the bullet list nor fail a dispatch (#1243).
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


def _one_line(value: Any) -> str:
    """``value`` as text with every whitespace run (CR/LF included) collapsed to one space."""
    return " ".join(str(value if value is not None else "").split())


def _line(item: dict[str, Any]) -> str:
    """One bullet line. Post: no newline in the result."""
    if item.get("kind") == "directive":
        return f"- [directive p{_one_line(item.get('priority', '?'))}] {_one_line(item.get('text'))}"
    parts = [f"- [board #{_one_line(item.get('rank', '?'))}] {_one_line(item.get('item'))}"]
    if item.get("tracking"):
        parts.append(f"(tracking {_one_line(item['tracking'])})")
    if item.get("acceptance"):
        parts.append(f"— done when: {_one_line(item['acceptance'])}")
    return " ".join(parts)


def focus_paragraph(repo: str, items: list[dict[str, Any]]) -> str:
    """Prompt paragraph with the items that apply to ``repo`` (all-repo directives included).

    Total: never raises (focus is advisory; ``StaffRunner.plan`` calls it unguarded).
    Post: empty string when nothing applies or an item cannot be rendered; otherwise a header
    plus at most ``MAX_ITEMS`` single-line bullets.
    """
    try:
        relevant = [i for i in items if isinstance(i, dict) and _applies(i, repo)][:MAX_ITEMS]
        lines = [_line(i) for i in relevant]
    except Exception as exc:  # noqa: BLE001 - a malformed item must not fail a dispatch
        log.warning("staff focus skipped: %s", exc)
        return ""
    if not lines:
        return ""
    return (
        "Fleet focus (latest board meeting and operator directives). When you choose between items, "
        "prefer work that advances these; do not start work that contradicts a directive:\n" + "\n".join(lines)
    )
