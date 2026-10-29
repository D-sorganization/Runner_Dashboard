"""Restart-stable fleet activity sessions projection (#2860, epic #2856 slice A4).

The projection is a pure function of persisted :class:`activity.ActivityEnvelope`
rows plus a ``now`` moment -- never an in-memory cache -- so it reproduces an
identical view across a process restart. See ``backend/activity_sessions.py``
for the documented late-event and stale/ended rules this file locks in.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from activity import ActivityEnvelope, ActivityStore


def envelope(session_id="session-a", seq=1, **overrides):
    values = dict(
        schema_version=1,
        event_id=f"{overrides.get('source', 'staff_run')}:{session_id}:{seq}",
        source="staff_run",
        principal="agent-a",
        node="node-a",
        session_id=session_id,
        seq=seq,
        ts="2026-10-28T12:00:00Z",
        kind="start",
        redaction_version=1,
        payload={},
    )
    values.update(overrides)
    values["event_id"] = f"{values['source']}:{session_id}:{seq}"
    return ActivityEnvelope.model_validate(values)


@pytest.fixture
def store(tmp_path):
    return ActivityStore(tmp_path / "activity.sqlite")


def ingest(store, *events):
    # append(), not ingest(): these tests exercise the sessions projection's
    # own read-side rules, not ingest()'s principal-binding gate, and several
    # cases deliberately mix principals/sources on one node or session_id.
    for event in events:
        store.append(event)


NOW = datetime(2026, 10, 28, 12, 10, 0, tzinfo=UTC)


def test_module_exists_and_exports_the_contract():
    import activity_sessions

    assert activity_sessions.compute_sessions
    assert activity_sessions.SESSIONS_SCAN_LIMIT > 0


def test_recent_heartbeat_is_live(store):
    ingest(store, envelope(ts="2026-10-28T12:09:00Z", kind="heartbeat"))
    from activity_sessions import compute_sessions

    page = compute_sessions(store, now=NOW)
    assert len(page.items) == 1
    assert page.items[0].status == "live"


def test_quiet_past_live_window_but_within_idle_window_is_idle(store):
    ingest(store, envelope(ts="2026-10-28T12:05:00Z", kind="heartbeat"))
    from activity_sessions import compute_sessions

    page = compute_sessions(store, now=NOW)
    assert page.items[0].status == "idle"


def test_heartbeat_absence_alone_yields_stale_never_ended(store):
    ingest(store, envelope(ts="2026-10-27T00:00:00Z", kind="heartbeat"))
    from activity_sessions import compute_sessions

    page = compute_sessions(store, now=NOW)
    assert page.items[0].status == "stale"


def test_explicit_end_event_yields_ended_even_if_recent(store):
    ingest(store, envelope(ts="2026-10-28T12:09:59Z", kind="exit"))
    from activity_sessions import compute_sessions

    page = compute_sessions(store, now=NOW)
    assert page.items[0].status == "ended"


@pytest.mark.parametrize(
    "source,terminal_kind",
    [
        ("staff_run", "exit"),
        ("staff_run", "cancel"),
        ("staff_run", "failed"),
        ("coordination", "presence_release"),
        ("checkin", "done"),
        ("recorder", "session_end"),
        ("recorder", "exit"),
    ],
)
def test_documented_terminal_kinds_per_source_end_the_session(store, source, terminal_kind):
    kwargs = {"source": source, "kind": terminal_kind, "ts": "2026-10-28T12:09:00Z"}
    if source == "checkin":
        kwargs["repo"] = "D-sorganization/Runner_Dashboard"
    ingest(store, envelope(**kwargs))
    from activity_sessions import compute_sessions

    page = compute_sessions(store, now=NOW)
    assert page.items[0].status == "ended"


def test_undocumented_source_never_derives_ended_from_kind_alone(store):
    # agent_bus has no explicit end event in the producer adapters (#2859); a kind
    # that merely *looks* terminal must not be treated as one (fail closed).
    ingest(store, envelope(source="agent_bus", kind="exit", ts="2026-10-28T12:09:00Z"))
    from activity_sessions import compute_sessions

    page = compute_sessions(store, now=NOW)
    assert page.items[0].status == "live"


def test_late_event_with_older_timestamp_does_not_override_a_newer_status(store):
    """Late-event rule: last-event-by-timestamp wins, not insertion/seq order."""
    ingest(
        store,
        envelope(seq=1, ts="2026-10-28T12:09:00Z", kind="heartbeat"),
    )
    # seq=2 is ingested *after* seq=1 but carries an *older* timestamp (a delayed
    # retry of an earlier beat that arrived late over a slow link).
    ingest(
        store,
        envelope(seq=2, ts="2026-10-27T00:00:00Z", kind="heartbeat"),
    )
    from activity_sessions import compute_sessions

    page = compute_sessions(store, now=NOW)
    assert len(page.items) == 1
    # The newer-by-timestamp event (seq=1) still determines status: live, not stale.
    assert page.items[0].status == "live"
    assert page.items[0].last_seq == 1


def test_out_of_order_delivery_resolves_to_the_newest_timestamp_regardless_of_seq(store):
    # seq=5 arrives first but is actually the newest event; seq=3 arrives after but
    # is older. Whichever is ingested first, the newest timestamp wins.
    ingest(store, envelope(seq=5, ts="2026-10-28T12:09:30Z", kind="heartbeat"))
    ingest(store, envelope(seq=3, ts="2026-10-28T11:00:00Z", kind="heartbeat"))
    from activity_sessions import compute_sessions

    page = compute_sessions(store, now=NOW)
    assert page.items[0].last_seq == 5
    assert page.items[0].status == "live"


def test_duplicate_retry_of_the_same_event_does_not_change_the_result(store):
    event = envelope(seq=1, ts="2026-10-28T12:09:00Z", kind="heartbeat")
    ingest(store, event)
    ingest(store, event)  # identical retry; the store dedupes it
    from activity_sessions import compute_sessions

    page = compute_sessions(store, now=NOW)
    assert len(page.items) == 1
    assert page.items[0].last_seq == 1


def test_restart_stability_two_independent_store_handles_agree(store):
    ingest(store, envelope(ts="2026-10-28T12:09:00Z", kind="heartbeat"))
    ingest(store, envelope(session_id="session-b", ts="2026-10-27T00:00:00Z", kind="heartbeat"))
    from activity_sessions import compute_sessions

    before = compute_sessions(store, now=NOW)
    reopened = ActivityStore(store.db_path)  # simulates a process restart: no shared memory
    after = compute_sessions(reopened, now=NOW)
    assert {item.session_id: item.status for item in before.items} == {
        item.session_id: item.status for item in after.items
    }


def test_filters_by_node_status_and_repo(store):
    ingest(
        store,
        envelope(session_id="session-a", node="node-a", ts="2026-10-28T12:09:00Z", kind="heartbeat", repo="r/a"),
    )
    ingest(
        store,
        envelope(
            session_id="session-b",
            node="node-b",
            ts="2026-10-27T00:00:00Z",
            kind="heartbeat",
            repo="r/b",
            principal="agent-b",
        ),
    )
    from activity_sessions import compute_sessions

    assert [item.session_id for item in compute_sessions(store, now=NOW, node="node-a").items] == ["session-a"]
    assert [item.session_id for item in compute_sessions(store, now=NOW, status="stale").items] == ["session-b"]
    assert [item.session_id for item in compute_sessions(store, now=NOW, repo="r/b").items] == ["session-b"]


def test_uncaptured_is_reserved_but_never_produced(store):
    ingest(store, envelope(ts="2026-10-28T12:09:00Z", kind="heartbeat"))
    from activity_sessions import compute_sessions

    page = compute_sessions(store, now=NOW, status="uncaptured")
    assert page.items == []


@pytest.mark.parametrize("limit", [0, -1, 501])
def test_limit_out_of_bounds_fails_closed(store, limit):
    from activity_sessions import compute_sessions

    with pytest.raises(ValueError):
        compute_sessions(store, now=NOW, limit=limit)


def test_response_is_bounded_by_limit(store):
    for index in range(5):
        ingest(
            store,
            envelope(
                session_id=f"session-{index}",
                ts="2026-10-28T12:09:00Z",
                kind="heartbeat",
                principal=f"agent-{index}",
            ),
        )
    from activity_sessions import compute_sessions

    page = compute_sessions(store, now=NOW, limit=2)
    assert len(page.items) == 2


def test_empty_store_reports_complete_empty(store):
    """#3503: a log that has never recorded anything must say so explicitly,
    not just return an empty page indistinguishable from a narrow filter."""
    from activity_sessions import compute_sessions

    page = compute_sessions(store, now=NOW)
    assert page.items == []
    assert page.empty_reason == "complete_empty"


def test_filtered_query_matching_nothing_reports_local_empty(store):
    """#3503: the log has data, but this call's own filter matched none of it --
    that must read as 'local_empty', never as 'no agents exist anywhere'."""
    ingest(store, envelope(node="node-a", ts="2026-10-28T12:09:00Z", kind="heartbeat"))
    from activity_sessions import compute_sessions

    page = compute_sessions(store, now=NOW, node="node-missing")
    assert page.items == []
    assert page.empty_reason == "local_empty"


def test_nonempty_page_has_no_empty_reason(store):
    ingest(store, envelope(ts="2026-10-28T12:09:00Z", kind="heartbeat"))
    from activity_sessions import compute_sessions

    page = compute_sessions(store, now=NOW)
    assert page.items
    assert page.empty_reason is None


def test_stale_session_is_excluded_from_live_status_filter(store):
    """#3503: a stale/expired session must never claim liveness; filtering for
    'live' must exclude it even though the store is otherwise non-empty."""
    ingest(store, envelope(ts="2026-10-27T00:00:00Z", kind="heartbeat"))
    from activity_sessions import compute_sessions

    page = compute_sessions(store, now=NOW, status="live")
    assert page.items == []
    assert page.empty_reason == "local_empty"


def test_sessions_are_grouped_by_source_and_session_id_not_just_session_id(store):
    # Two different sources can legitimately reuse the same session_id string; they
    # must not be merged into one row (DRY on the store's own uniqueness contract,
    # which keys on (source, session_id, seq), never session_id alone).
    ingest(store, envelope(source="staff_run", session_id="shared", ts="2026-10-28T12:09:00Z", kind="heartbeat"))
    ingest(
        store,
        envelope(
            source="coordination",
            session_id="shared",
            ts="2026-10-28T12:09:00Z",
            kind="presence_register",
            principal="agent-b",
        ),
    )
    from activity_sessions import compute_sessions

    page = compute_sessions(store, now=NOW)
    assert {item.source for item in page.items} == {"staff_run", "coordination"}
    assert len(page.items) == 2
