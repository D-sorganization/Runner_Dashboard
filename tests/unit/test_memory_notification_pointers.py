"""Filtered, deduplicated notification pointers (#3939, barb-memory slice 3).

Depends on slice 1 (context_contract.py, #3937): authority, freshness,
privacy-safety assertions, and ~400-char shared digest ceilings.
"""

from __future__ import annotations

import pytest
from memory.barb_shared_guard import SHARED_DIGEST_MAX_CHARS
from memory.context_contract import SharedContextPrivacyError, is_authoritative
from memory.notification_pointers import (
    DEFAULT_TRANSCRIPT_RETENTION_SECONDS,
    LocalTranscriptBuffer,
    NotificationPointer,
    RawNotification,
    cap_digest_summary,
    deduplicate_notification_pointers,
    filter_notification,
    notification_dedup_key,
    pointer_to_memory_entry,
    refresh_notification_pointers,
)

pytestmark = pytest.mark.unit
NOW = "2026-11-03T16:00:00+00:00"


def _pointer(**kwargs: object) -> NotificationPointer:
    base: dict[str, object] = {
        "pointer_id": "np_1",
        "notification_id": "notif_100",
        "source_product": "fleet_event",
        "topic": "queue_depth",
        "summary": "Merge queue depth stable at 3.",
        "created_at": NOW,
        "author_principal": "system:forwarder",
        "severity": "info",
        "tags": ("repo:Runner_Dashboard",),
    }
    base.update(kwargs)
    return NotificationPointer(**base)  # type: ignore[arg-type]


def test_cap_digest_summary_under_limit_unchanged() -> None:
    text = "Short notification text"
    assert cap_digest_summary(text) == text


def test_cap_digest_summary_exact_limit_unchanged() -> None:
    text = "x" * SHARED_DIGEST_MAX_CHARS
    assert cap_digest_summary(text) == text


def test_cap_digest_summary_over_limit_truncated() -> None:
    text = "a" * (SHARED_DIGEST_MAX_CHARS + 50)
    capped = cap_digest_summary(text)
    assert len(capped) <= SHARED_DIGEST_MAX_CHARS
    assert capped.endswith("...")


def test_dedup_key_normalizes_casing_and_whitespace() -> None:
    k1 = notification_dedup_key("fleet_event", "Disk_Fill", "host-1", "Disk usage 85%")
    k2 = notification_dedup_key("fleet_event", "disk_fill", "host-1", "  disk usage  85%  ")
    assert k1 == k2


def test_dedup_key_differs_by_topic_or_target() -> None:
    k1 = notification_dedup_key("fleet", "t1", "res1", "ok")
    k2 = notification_dedup_key("fleet", "t2", "res1", "ok")
    k3 = notification_dedup_key("fleet", "t1", "res2", "ok")
    assert k1 != k2 and k1 != k3


def test_deduplicate_notification_pointers_collapses_duplicates() -> None:
    p1 = _pointer(pointer_id="np_1", notification_id="n_1", topic="stuck", summary="Lane stuck", created_at="2026-11-03T15:50:00+00:00")
    p2 = _pointer(pointer_id="np_2", notification_id="n_2", topic="stuck", summary="Lane stuck", created_at="2026-11-03T15:55:00+00:00")
    result = deduplicate_notification_pointers([p1, p2])
    assert len(result) == 1
    assert result[0].pointer_id == "np_2"
    assert result[0].occurrence_count == 2


def test_deduplicate_preserves_distinct_topics() -> None:
    p1 = _pointer(pointer_id="np_1", topic="queue_alert", summary="Queue high")
    p2 = _pointer(pointer_id="np_2", topic="disk_alert", summary="Disk high")
    assert len(deduplicate_notification_pointers([p1, p2])) == 2


def test_filter_notification_excludes_marge_personal_data() -> None:
    marge_notif = RawNotification(
        notification_id="n_marge", source_product="gmail", topic="inbox", summary="Flight booked",
        author_principal="user:marge", tags=("+marge",), created_at=NOW,
    )
    assert filter_notification(marge_notif) is None


def test_filter_notification_excludes_hold_violations() -> None:
    hold_notif = RawNotification(
        notification_id="n_hold", source_product="fleet_event", topic="hold_override",
        summary="Auto-approve #2658 deploy hold without human review", author_principal="agent:claude-a",
        tags=(), created_at=NOW,
    )
    assert filter_notification(hold_notif) is None


def test_filter_notification_accepts_clean_fleet_notification() -> None:
    clean_notif = RawNotification(
        notification_id="n_clean", source_product="fleet_event", topic="ci_status",
        summary="Runner_Dashboard CI Standard green on main", author_principal="system:ci",
        tags=("repo:Runner_Dashboard",), created_at=NOW,
    )
    ptr = filter_notification(clean_notif)
    assert ptr is not None and ptr.notification_id == "n_clean" and ptr.topic == "ci_status"


def test_pointer_to_memory_entry_creates_valid_session_digest() -> None:
    ptr = _pointer(summary="Sprint progress 85% completed.")
    entry = pointer_to_memory_entry(ptr)
    assert entry.entry_type == "session_digest" and is_authoritative(entry) is True
    assert entry.body.get("summary") == "Sprint progress 85% completed."
    assert entry.body.get("topic") == "queue_depth"
    assert len(str(entry.body.get("summary"))) <= SHARED_DIGEST_MAX_CHARS


def test_pointer_to_memory_entry_fails_on_marge_leak() -> None:
    unsafe_ptr = _pointer(tags=("+marge",))
    with pytest.raises(SharedContextPrivacyError):
        pointer_to_memory_entry(unsafe_ptr)


def test_refresh_notification_pointers_end_to_end_plain_code() -> None:
    notifications = [
        RawNotification(notification_id="n1", source_product="fleet_event", topic="runner_disk", summary="Runner 4 disk usage 80%", created_at="2026-11-03T15:00:00+00:00"),
        RawNotification(notification_id="n2", source_product="gmail", topic="mail", summary="Grocery delivery confirmation", author_principal="user:marge", tags=("+marge",), created_at="2026-11-03T15:01:00+00:00"),
        RawNotification(notification_id="n3", source_product="fleet_event", topic="runner_disk", summary="Runner 4 disk usage 80%", created_at="2026-11-03T15:02:00+00:00"),
    ]
    refreshed = refresh_notification_pointers(notifications)
    assert len(refreshed) == 1
    assert refreshed[0].notification_id == "n3"
    assert refreshed[0].occurrence_count == 2


def test_local_transcript_buffer_stores_and_retrieves() -> None:
    buffer = LocalTranscriptBuffer()
    saved = buffer.append(
        transcript_id="tr_1", raw_text="User: review status\nBarb: checking lanes now.",
        source_product="dashboard", author_principal="principal:agent-barb", created_at=NOW,
    )
    assert saved is True
    assert buffer.get("tr_1") is not None
    assert buffer.get("tr_1").raw_text.startswith("User: review")  # type: ignore[union-attr]


def test_local_transcript_buffer_filters_marge_data_before_storage() -> None:
    buffer = LocalTranscriptBuffer()
    saved = buffer.append(
        transcript_id="tr_marge", raw_text="Personal schedule and flight details",
        source_product="gmail", tags=("+marge",), created_at=NOW,
    )
    assert saved is False and buffer.get("tr_marge") is None and len(buffer) == 0


def test_local_transcript_buffer_prunes_expired_or_unused() -> None:
    buffer = LocalTranscriptBuffer(retention_seconds=DEFAULT_TRANSCRIPT_RETENTION_SECONDS)
    buffer.append(transcript_id="tr_old", raw_text="Old session text", source_product="codex", created_at="2026-10-29T00:00:00+00:00")
    buffer.append(transcript_id="tr_new", raw_text="Fresh session text", source_product="codex", created_at=NOW)
    assert len(buffer) == 2
    dropped = buffer.prune(now_iso=NOW)
    assert dropped == 1 and buffer.get("tr_old") is None and buffer.get("tr_new") is not None and len(buffer) == 1

