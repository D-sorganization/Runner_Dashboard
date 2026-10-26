"""Run-event storage helpers for schema evolution and text integrity."""

from __future__ import annotations

import sqlite3

from staff.lesson_evidence import LESSON_EVIDENCE_ASSISTANT_ANSWER
from staff.redaction import redact_sensitive_content

MAX_EVENT_TEXT_CHARS = 4000


def prepare_lesson_evidence(text: str, marker: str | None) -> tuple[str, str | None]:
    """Redact event text and discard its evidence marker if storage changes or truncates it."""
    if marker not in (None, LESSON_EVIDENCE_ASSISTANT_ANSWER):
        raise ValueError("invalid lesson evidence marker")
    redacted = redact_sensitive_content(text)
    if marker is not None and (redacted != text or len(redacted) > MAX_EVENT_TEXT_CHARS):
        marker = None
    return redacted, marker


def migrate_event_evidence(conn: sqlite3.Connection) -> None:
    """Add the nullable, closed-vocabulary marker; legacy event rows stay unclassified."""
    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(events)").fetchall()}
    if "lesson_evidence" not in columns:
        conn.execute(
            "ALTER TABLE events ADD COLUMN lesson_evidence TEXT "
            "CHECK (lesson_evidence IS NULL OR lesson_evidence = 'assistant_answer')"
        )


def latest_result_line(conn: sqlite3.Connection, run_id: str) -> str:
    row = conn.execute(
        "SELECT text FROM events WHERE run_id = ? AND text LIKE '%STAFF_RESULT:%' ORDER BY seq DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    return str(row[0]) if row else ""
