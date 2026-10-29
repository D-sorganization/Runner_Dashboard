from __future__ import annotations

import sqlite3

import activity
import pytest
from activity import ActivityEnvelope, ActivityStore
from fastapi import HTTPException


def envelope(**overrides: object) -> ActivityEnvelope:
    values: dict[str, object] = {
        "schema_version": 1,
        "event_id": "event-1",
        "source": "recorder",
        "principal": "agent-a",
        "node": "node-a",
        "session_id": "session-a",
        "seq": 1,
        "ts": "2026-10-25T12:00:00Z",
        "kind": "session.started",
        "redaction_version": 1,
        "payload": {"state": "started"},
    }
    values.update(overrides)
    return ActivityEnvelope.model_validate(values)


def test_unknown_schema_version_fails_closed() -> None:
    with pytest.raises(ValueError):
        envelope(schema_version=2)


def test_append_is_idempotent_but_conflicting_retry_is_visible(tmp_path) -> None:
    store = ActivityStore(tmp_path / "activity.sqlite")
    first = store.append(envelope())
    assert store.append(envelope()) == first
    with pytest.raises(HTTPException) as exc:
        store.append(envelope(payload={"state": "different"}))
    assert exc.value.status_code == 409


def test_existing_rows_cannot_be_updated_or_deleted(tmp_path) -> None:
    store = ActivityStore(tmp_path / "activity.sqlite")
    store.append(envelope())
    conn = store._connection()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE activity_events SET envelope='{}'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM activity_events")
    conn.close()


def test_read_is_filtered_bounded_and_cursor_paginated(tmp_path) -> None:
    store = ActivityStore(tmp_path / "activity.sqlite")
    store.append(envelope())
    store.append(envelope(event_id="event-2", seq=2, kind="OverflowGap"))
    page = store.read(limit=1)
    assert len(page.items) == 1
    assert page.next_cursor == 1
    second = store.read(since=page.next_cursor)
    assert second.items[0].kind == "OverflowGap"
    assert store.read(repo="other").items == []


def test_scan_recent_is_bounded_newest_first_and_filterable(tmp_path) -> None:
    # #2860's sessions projection reads through this primitive instead of an
    # unbounded table scan; lock in that it stays bounded, ordered and indexed-filterable.
    store = ActivityStore(tmp_path / "activity.sqlite")
    store.append(envelope(event_id="event-1", seq=1, node="node-a", repo="repo-a"))
    store.append(envelope(event_id="event-2", seq=2, node="node-b", repo="repo-b"))
    store.append(envelope(event_id="event-3", seq=3, node="node-a", repo="repo-a"))
    newest_first = store.scan_recent(limit=10)
    assert [item.event_id for item in newest_first] == ["event-3", "event-2", "event-1"]
    assert [item.event_id for item in store.scan_recent(limit=1)] == ["event-3"]
    assert [item.event_id for item in store.scan_recent(node="node-b")] == ["event-2"]
    assert [item.event_id for item in store.scan_recent(repo="repo-a")] == ["event-3", "event-1"]


def test_scan_recent_rejects_out_of_bounds_limit(tmp_path) -> None:
    store = ActivityStore(tmp_path / "activity.sqlite")
    with pytest.raises(ValueError):
        store.scan_recent(limit=0)
    with pytest.raises(ValueError):
        store.scan_recent(limit=activity.MAX_SCAN_LIMIT + 1)
