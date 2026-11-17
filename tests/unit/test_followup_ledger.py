"""Direct contract tests for ``staff.followup_ledger`` (BR-04 durable follow-up state).

The follow-up engine tests exercise the ledger through a sweep; these pin the ledger's own
contracts: one winner per due window (also across threads and a restart), release only by
the claimer, ordered history, counts by kind, and the sweep backlog arithmetic.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from staff.followup_ledger import FollowupLedger, LedgerRecord, SweepBacklog, stamp

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def _s(minutes: float) -> str:
    return stamp(T0 + timedelta(minutes=minutes))


def _rec(rec_id: str, ts: str, action: str = "nudge", target: str = "wi-1") -> LedgerRecord:
    return LedgerRecord(rec_id, target, "work_item", "stalled", action, {"n": 1}, ts)


@pytest.fixture
def ledger(tmp_path: Path) -> FollowupLedger:
    return FollowupLedger(tmp_path / "staff.sqlite3")


def test_stamp_is_fixed_width_utc() -> None:
    assert stamp(T0) == "2026-11-16T12:00:00.000000Z"
    assert len(stamp(T0)) == len(stamp(T0 + timedelta(microseconds=1)))


def test_claim_wins_once_per_due_window(ledger: FollowupLedger) -> None:
    assert ledger.claim("wi-1", "work_item", _s(0), _s(5), "w1") is True
    assert ledger.claim("wi-1", "work_item", _s(0), _s(5), "w2") is False
    assert ledger.claim("wi-1", "work_item", _s(4), _s(9), "w2") is False
    assert ledger.claim("wi-1", "work_item", _s(5), _s(10), "w2") is True


def test_claim_rejects_a_window_that_moves_backwards(ledger: FollowupLedger) -> None:
    with pytest.raises(ValueError, match="move the next check forward"):
        ledger.claim("wi-1", "work_item", _s(5), _s(0), "w1")
    assert ledger.is_due("wi-1", _s(0)) is True


def test_concurrent_claims_have_exactly_one_winner(ledger: FollowupLedger) -> None:
    start = threading.Barrier(8)
    wins: list[bool] = []

    def worker(name: str) -> None:
        start.wait()
        wins.append(ledger.claim("wi-1", "work_item", _s(0), _s(5), name))

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(wins) == [False] * 7 + [True]


def test_claim_survives_a_restart(tmp_path: Path) -> None:
    db = tmp_path / "staff.sqlite3"
    assert FollowupLedger(db).claim("wi-1", "work_item", _s(0), _s(5), "w1") is True
    restarted = FollowupLedger(db)
    assert restarted.claim("wi-1", "work_item", _s(1), _s(6), "w2") is False
    assert restarted.is_due("wi-1", _s(5)) is True


def test_is_due_for_unknown_and_claimed_targets(ledger: FollowupLedger) -> None:
    assert ledger.is_due("never-seen", _s(0)) is True
    ledger.claim("wi-1", "work_item", _s(0), _s(5), "w1")
    assert ledger.is_due("wi-1", _s(4)) is False
    assert ledger.is_due("wi-1", _s(5)) is True


def test_release_by_the_claimer_makes_the_target_due_again(ledger: FollowupLedger) -> None:
    ledger.claim("wi-1", "work_item", _s(0), _s(5), "w1")
    ledger.release("wi-1", _s(1), "w1")
    assert ledger.is_due("wi-1", _s(1)) is True
    assert ledger.claim("wi-1", "work_item", _s(1), _s(6), "w2") is True


def test_release_by_another_worker_is_a_no_op(ledger: FollowupLedger) -> None:
    ledger.claim("wi-1", "work_item", _s(0), _s(5), "w1")
    ledger.release("wi-1", _s(1), "w2")
    assert ledger.is_due("wi-1", _s(1)) is False


def test_records_round_trip_in_timestamp_order(ledger: FollowupLedger) -> None:
    ledger.record(_rec("b", _s(2)))
    ledger.record(_rec("a", _s(1), action="escalate"))
    ledger.record(_rec("c", _s(0), target="wi-2"))
    got = ledger.records_for("wi-1")
    assert [r.id for r in got] == ["a", "b"]
    assert got[0] == _rec("a", _s(1), action="escalate")
    assert ledger.records_for("missing") == []


def test_record_updates_the_targets_last_action(ledger: FollowupLedger, tmp_path: Path) -> None:
    ledger.claim("wi-1", "work_item", _s(0), _s(5), "w1")
    ledger.record(_rec("a", _s(1), action="escalate"))
    with sqlite3.connect(tmp_path / "staff.sqlite3") as conn:
        row = conn.execute(
            "SELECT last_action, last_action_at FROM followup_checks WHERE target_id = 'wi-1'"
        ).fetchone()
    assert row == ("escalate", _s(1))


def test_record_serialises_non_json_detail_as_text(ledger: FollowupLedger) -> None:
    ledger.record(LedgerRecord("a", "wi-1", "work_item", "stalled", "nudge", {"at": T0}, _s(0)))
    assert ledger.records_for("wi-1")[0].detail == {"at": str(T0)}


def test_action_counts_include_only_records_since(ledger: FollowupLedger) -> None:
    ledger.record(_rec("old", _s(-10)))
    ledger.record(_rec("a", _s(0)))
    ledger.record(_rec("b", _s(1)))
    ledger.record(_rec("c", _s(2), action="escalate"))
    assert ledger.action_counts(_s(0)) == {"nudge": 2, "escalate": 1}
    assert ledger.action_counts(_s(3)) == {}


def test_backlog_counts_active_items_without_deadlines() -> None:
    backlog = SweepBacklog()
    backlog.observe(None, _s(0), T0)
    backlog.observe("", _s(0), T0)
    assert (backlog.active, backlog.overdue, backlog.oldest_due_at) == (2, 0, None)


def test_backlog_future_and_exactly_now_deadlines_are_not_overdue() -> None:
    backlog = SweepBacklog()
    backlog.observe(_s(5), _s(0), T0)
    backlog.observe(_s(0), _s(0), T0)
    assert (backlog.active, backlog.overdue) == (2, 0)


def test_backlog_first_overdue_item_is_the_oldest() -> None:
    backlog = SweepBacklog()
    backlog.observe(_s(-30), _s(0), T0)
    backlog.observe(_s(-10), _s(0), T0)
    assert backlog.overdue == 2
    assert backlog.oldest_due_at == _s(-30)
    assert backlog.oldest_due_age_seconds == 30 * 60


def test_backlog_treats_naive_deadlines_as_utc() -> None:
    backlog = SweepBacklog()
    backlog.observe("2026-11-16T11:59:00", _s(0), T0)
    assert backlog.oldest_due_age_seconds == 60


def test_backlog_to_dict_has_every_field() -> None:
    backlog = SweepBacklog(active=3, overdue=1, oldest_due_at=_s(-1), oldest_due_age_seconds=60)
    assert backlog.to_dict() == {
        "active": 3,
        "overdue": 1,
        "oldest_due_at": _s(-1),
        "oldest_due_age_seconds": 60,
        "decisions_overdue": 0,
        "duration_ms": 0,
    }
