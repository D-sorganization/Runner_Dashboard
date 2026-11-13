"""Read-only, bounded locators for persisted marked lesson directives (#2664)."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from staff.lessons import parse_staff_lessons
from staff.store import RunStore

PAGE_SIZE = 100
MAX_SCAN_EVENTS = 1000
_FIELDS = {"version", "event_seq", "line_index", "line_sha256"}


def _marked_events(store: RunStore, run_id: str) -> list[dict[str, Any]]:
    """Read the entire bounded window before trusting even an early match."""
    events: list[dict[str, Any]] = []
    after = 0
    scanned = 0
    while True:
        page = store.events_after(run_id, after, limit=PAGE_SIZE, with_evidence=True)
        if not page:
            return events
        scanned += len(page)
        if scanned > MAX_SCAN_EVENTS:
            raise ValueError("lesson evidence scan limit exceeded")
        events.extend(event for event in page if event.get("lesson_evidence") == "assistant_answer")
        after = page[-1]["seq"]


def locate_lesson_evidence(store: RunStore, run_id: str, repo: str, lesson: str) -> dict[str, Any] | None:
    """Derive the first exact eligible directive from the authoritative run.

    A locator has version 1, a positive event sequence, a zero-based event-local
    line index and SHA-256 of the stripped full directive line (UTF-8). No caller
    event list or transcript path is accepted. Scan overflow raises ValueError.
    """
    if any(type(value) is not str or not value for value in (run_id, repo, lesson)):
        raise ValueError("run_id, repo and lesson must be non-empty strings")
    rec = store.get_run(run_id)
    if rec is None or rec.repo != repo:
        return None
    events = _marked_events(store, run_id)
    lessons = parse_staff_lessons(events)
    if lesson not in lessons:
        return None
    ordinal = lessons.index(lesson)

    # Parsed lesson count is monotonic over prefixes, even across fences. Binary
    # search preserves the existing parser's rules without duplicating them or
    # reparsing every line of a potentially large scan window.
    low, high = 1, len(events)
    while low < high:
        middle = (low + high) // 2
        if len(parse_staff_lessons(events[:middle])) > ordinal:
            high = middle
        else:
            low = middle + 1
    event_index = low - 1
    event = events[event_index]
    lines = event["text"].splitlines()
    low, high = 1, len(lines)
    while low < high:
        middle = (low + high) // 2
        prefix = events[:event_index] + [{**event, "text": "\n".join(lines[:middle])}]
        if len(parse_staff_lessons(prefix)) > ordinal:
            high = middle
        else:
            low = middle + 1
    return {
        "version": 1,
        "event_seq": event["seq"],
        "line_index": low - 1,
        "line_sha256": hashlib.sha256(lines[low - 1].strip().encode("utf-8")).hexdigest(),
    }


def verify_lesson_evidence(store: RunStore, run_id: str, repo: str, lesson: str, locator: object) -> bool:
    """Validate the closed locator shape, then rederive before comparing."""
    if type(locator) is not dict or set(locator) != _FIELDS:
        return False
    if type(locator["version"]) is not int or locator["version"] != 1:
        return False
    for field, minimum in (("event_seq", 1), ("line_index", 0)):
        if type(locator[field]) is not int or locator[field] < minimum:
            return False
    digest = locator["line_sha256"]
    if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return False
    return locate_lesson_evidence(store, run_id, repo, lesson) == locator
