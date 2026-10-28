"""Unit + integration tests for the terminal-run-card notifier (#3052)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path

import pytest
from staff.conversations import ConversationStore
from staff.run_notifier import (
    DIGEST_THRESHOLD,
    NotifierPrefs,
    OutboxSink,
    RunEvent,
    Sink,
    deliver,
    is_private_repo,
    plan,
    verified_outcome,
)

NOW = datetime(2026, 10, 27, 12, 0, 0)
DEFAULT_PREFS = NotifierPrefs()


def _event(run_id: str = "run-1", **overrides: object) -> RunEvent:
    fields: dict[str, object] = {
        "run_id": run_id,
        "terminal_state": "success",
        "role": "issue-remediator",
        "repo": "Runner_Dashboard",
        "issue_ref": "#3052",
        "occurred_at": NOW,
        "status": "succeeded",
        "verification": "verified",
        "duration_s": 42.0,
        "provider": "claude",
        "host": "OGLaptop",
        "summary": "Added the run-card notifier.",
        "transcript_url": "https://example.invalid/transcripts/run-1",
    }
    fields.update(overrides)
    return RunEvent(**fields)  # type: ignore[arg-type]


class _FakeSink(Sink):
    def __init__(self, *, public: bool = False) -> None:
        self.public = public
        self.sent: list[object] = []

    def send(self, notification: object) -> None:
        self.sent.append(notification)


def test_verified_outcome_uses_run_outcome_buckets() -> None:
    assert verified_outcome(_event(status="succeeded", verification="verified")) == "success"
    assert verified_outcome(_event(status="failed", verification="failed")) == "quality_fail"
    assert verified_outcome(_event(status="blocked")) == "infra_fail"


def test_verified_outcome_downgrades_an_unverified_success() -> None:
    event = _event(status="succeeded", verification="")
    assert verified_outcome(event) == "unverified"


def test_replaying_the_same_terminal_event_produces_no_second_notification() -> None:
    event = _event()
    delivered = {(event.run_id, event.terminal_state)}
    assert plan([event], NOW, DEFAULT_PREFS, delivered=delivered) == []


def test_a_fresh_event_produces_exactly_one_card() -> None:
    notifications = plan([_event()], NOW, DEFAULT_PREFS)
    assert len(notifications) == 1
    assert notifications[0].kind == "card"
    assert notifications[0].run_ids == ("run-1",)


def test_more_than_the_threshold_in_the_window_collapses_to_one_digest() -> None:
    events = [
        _event(
            run_id=f"run-{i}",
            role=f"role-{i}" if i < 2 else "issue-remediator",
            terminal_state="quality_fail" if i < 2 else "success",
            status="failed" if i < 2 else "succeeded",
            verification="failed" if i < 2 else "verified",
        )
        for i in range(DIGEST_THRESHOLD + 1)
    ]
    notifications = plan(events, NOW, DEFAULT_PREFS)
    assert len(notifications) == 1
    assert notifications[0].kind == "digest"
    assert "role-0/Runner_Dashboard" in notifications[0].text
    assert "role-1/Runner_Dashboard" in notifications[0].text


def test_at_the_threshold_every_event_still_gets_its_own_card() -> None:
    events = [_event(run_id=f"run-{i}") for i in range(DIGEST_THRESHOLD)]
    notifications = plan(events, NOW, DEFAULT_PREFS)
    assert len(notifications) == DIGEST_THRESHOLD
    assert all(n.kind == "card" for n in notifications)


def test_events_older_than_the_backlog_cutoff_collapse_into_one_backlog_digest() -> None:
    stale = [_event(run_id=f"old-{i}", occurred_at=NOW - timedelta(hours=30)) for i in range(3)]
    notifications = plan(stale, NOW, DEFAULT_PREFS)
    assert len(notifications) == 1
    assert notifications[0].kind == "backlog_digest"
    assert set(notifications[0].run_ids) == {"old-0", "old-1", "old-2"}


def test_a_mixed_backlog_and_recent_flood_produce_two_independent_digests() -> None:
    stale = [_event(run_id=f"old-{i}", occurred_at=NOW - timedelta(hours=30)) for i in range(3)]
    flood = [_event(run_id=f"new-{i}") for i in range(DIGEST_THRESHOLD + 1)]
    notifications = plan(stale + flood, NOW, DEFAULT_PREFS)
    kinds = sorted(n.kind for n in notifications)
    assert kinds == ["backlog_digest", "digest"]


@pytest.mark.parametrize(
    ("now_time", "expected"),
    [
        (time(2, 0), False),  # inside the default 00:00-06:00 PT quiet window: suppressed
        (time(12, 0), True),
        (time(23, 59), True),
    ],
)
def test_a_quality_failure_notifies_dieter_only_outside_quiet_hours(now_time: time, expected: bool) -> None:
    at = datetime.combine(NOW.date(), now_time)
    event = _event(terminal_state="quality_fail", status="failed", verification="failed")
    [notification] = plan([event], at, DEFAULT_PREFS)
    assert notification.notify_dieter is expected


def test_always_notify_dieter_overrides_quiet_hours() -> None:
    at = datetime.combine(NOW.date(), time(2, 0))
    event = _event(terminal_state="needs_owner", status="blocked", always_notify_dieter=True)
    [notification] = plan([event], at, DEFAULT_PREFS)
    assert notification.notify_dieter is True


def test_a_successful_run_never_notifies_dieter_even_outside_quiet_hours() -> None:
    at = datetime.combine(NOW.date(), time(12, 0))
    [notification] = plan([_event()], at, DEFAULT_PREFS)
    assert notification.notify_dieter is False


def test_a_card_is_capped_at_roughly_300_tokens() -> None:
    from staff.run_notifier import CARD_CHAR_CAP

    event = _event(summary="x" * 5000)
    [notification] = plan([event], NOW, DEFAULT_PREFS)
    assert len(notification.text) <= CARD_CHAR_CAP


@pytest.mark.parametrize(
    ("repo", "expected"),
    [
        ("Gasification_Model", True),
        ("Runner_Dashboard_Private", True),
        ("Runner_Dashboard", False),
    ],
)
def test_is_private_repo(repo: str, expected: bool) -> None:
    assert is_private_repo(repo) is expected


def test_a_private_repo_card_is_refused_by_any_sink_marked_public() -> None:
    event = _event(repo="Gasification_Model")
    [notification] = plan([event], NOW, DEFAULT_PREFS)
    assert notification.private is True

    public_sink, private_sink = _FakeSink(public=True), _FakeSink(public=False)
    used = deliver(notification, [public_sink, private_sink])

    assert public_sink.sent == []
    assert private_sink.sent == [notification]
    assert used == [private_sink]


def test_a_public_repo_card_reaches_every_sink() -> None:
    [notification] = plan([_event()], NOW, DEFAULT_PREFS)
    sinks = [_FakeSink(public=True), _FakeSink(public=False)]
    used = deliver(notification, sinks)
    assert used == sinks
    assert all(sink.sent == [notification] for sink in sinks)


def test_a_terminal_transition_yields_exactly_one_outbox_entry(tmp_path: Path) -> None:
    conversations = ConversationStore(tmp_path / "node.sqlite3")
    outbox = conversations.barb_outbox(max_entries=50, max_bytes=1_000_000, max_entry_bytes=10_000)
    sink = OutboxSink(outbox, thread_id="thr-barb")

    [notification] = plan([_event()], NOW, DEFAULT_PREFS)
    deliver(notification, [sink])

    assert outbox.depth() == 1
    [entry] = outbox.pending_for("thr-barb", "run-1")
    assert entry.kind == "run_card"
    assert entry.terminal is True


def test_replaying_the_same_terminal_card_through_the_outbox_sink_stays_at_one_entry(tmp_path: Path) -> None:
    conversations = ConversationStore(tmp_path / "node.sqlite3")
    outbox = conversations.barb_outbox(max_entries=50, max_bytes=1_000_000, max_entry_bytes=10_000)
    sink = OutboxSink(outbox, thread_id="thr-barb")

    [notification] = plan([_event()], NOW, DEFAULT_PREFS)
    deliver(notification, [sink])
    deliver(notification, [sink])  # replay: restart, outbox redelivery, etc.

    assert outbox.depth() == 1
