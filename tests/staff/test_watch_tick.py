"""Tests for the non-LLM watch tick (issue #3053): table tests for
:func:`evaluate`, the ETag cache, rate-limit backoff, and integration tests
driving :class:`WatchTickRunner` against fakes + a real ``IdempotencyStore``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from staff.idempotency import IdempotencyStore
from staff.watch_tick import (
    Escalation,
    ETagCache,
    ProbeResponse,
    RateLimitState,
    RepoSnapshot,
    WatchThresholds,
    WatchTickRunner,
    evaluate,
    watch_tick_enabled,
)

REPO = "D-sorganization/Runner_Dashboard"
NOW = time.time()


def _snap(queued=0, age=0, failed=(), unanswered=(), failures=()) -> RepoSnapshot:  # type: ignore[no-untyped-def]
    return RepoSnapshot(
        repo=REPO,
        queued_count=queued,
        oldest_age_seconds=age,
        failed_heads=failed,
        unanswered_to_barb=unanswered,
        failure_timestamps=failures,
    )


# (previous, current, expected (role, trigger) or None) — P = pytest.param, kept short so each row is one line.
P = pytest.param
FH, QS, NW = "pr-remediator", "queue-shepherd", "night-watch"
TABLE = [
    P(_snap(queued=1, age=60), _snap(queued=1, age=60), None, id="no_change"),
    P(None, _snap(queued=1, age=5), None, id="no_previous_tick_under_threshold"),
    P(_snap(failed=("x",)), _snap(failed=("x",)), (FH, "merge_queue_failed_head"), id="head_failed_twice"),
    P(_snap(failed=()), _snap(failed=("x",)), None, id="head_failed_once_no_escalation"),
    P(_snap(queued=1, age=900), _snap(queued=1, age=900), (QS, "merge_queue_age"), id="queue_too_old"),
    P(_snap(queued=1, age=100), _snap(queued=1, age=100), None, id="queue_age_under_threshold"),
    P(None, _snap(failures=(NOW - 10, NOW - 20, NOW - 30)), (NW, "failing_run_storm"), id="storm"),
    P(None, _snap(failures=(NOW - 10, NOW - 20)), None, id="storm_below_count"),
    P(_snap(unanswered=()), _snap(unanswered=("t1",)), (NW, "board_new_unanswered"), id="new_board_message"),
    P(_snap(failed=("x",)), _snap(failed=()), None, id="recovery_clears_prior_failure"),
]


@pytest.mark.unit
@pytest.mark.parametrize(("prev", "cur", "expect"), TABLE)
def test_evaluate_table(prev: RepoSnapshot | None, cur: RepoSnapshot, expect: tuple[str, str] | None) -> None:
    thresholds = WatchThresholds(queue_age_seconds=600, storm_count=3, storm_window_seconds=900)
    previous = {REPO: prev} if prev is not None else None
    out = evaluate(previous, {REPO: cur}, thresholds)
    if expect is None:
        assert out == []
    else:
        assert [(e.role, e.trigger) for e in out] == [expect]


@pytest.mark.unit
def test_evaluate_fails_closed_on_malformed_input() -> None:
    assert evaluate({}, "not-a-dict", WatchThresholds()) == []  # type: ignore[arg-type]
    assert evaluate({}, {REPO: "not-a-snapshot"}, WatchThresholds()) == []  # type: ignore[dict-item]


@pytest.mark.unit
def test_escalation_idempotency_key_and_role_validation() -> None:
    esc = Escalation(role="night-watch", repo=REPO, trigger="board_new_unanswered", reason="x", hour_bucket="h10")
    assert esc.idempotency_key() == f"watch-tick:night-watch:{REPO}:board_new_unanswered:h10"
    with pytest.raises(ValueError):
        Escalation(role="bad-role", repo=REPO, trigger="x", reason="x", hour_bucket="h")  # type: ignore[arg-type]


@pytest.mark.unit
def test_etag_cache_remembers_non_empty_etags_only() -> None:
    cache = ETagCache()
    assert cache.etag_for("queue:repo") is None
    cache.remember("queue:repo", None)
    assert cache.etag_for("queue:repo") is None
    cache.remember("queue:repo", 'W/"abc123"')
    assert cache.etag_for("queue:repo") == 'W/"abc123"'
    assert ProbeResponse(status_code=304, etag='W/"abc123"', body=None).body is None


@pytest.mark.unit
def test_rate_limit_trip_disables_until_reset_and_logs_once(caplog: pytest.LogCaptureFixture) -> None:
    state = RateLimitState()
    assert not state.is_disabled(time.time())
    with caplog.at_level("WARNING"):
        state.trip(time.time() + 3600)
    assert state.is_disabled(time.time())
    assert sum("rate limit" in rec.message for rec in caplog.records) == 1
    state.trip(time.time() - 1)  # a past reset re-enables probing
    assert not state.is_disabled(time.time())


def test_feature_flag_default_off_opt_in_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAFF_WATCH_TICK_ENABLED", raising=False)
    assert watch_tick_enabled() is False
    monkeypatch.setenv("STAFF_WATCH_TICK_ENABLED", "1")
    assert watch_tick_enabled() is True


# --- Integration: tick + fake dispatch port ---


@dataclass
class _FakeHolds:
    blocked: dict[tuple[str, str], str] = field(default_factory=dict)

    def blocking(self, role: str, repo: str) -> str | None:
        return self.blocked.get((role, repo))


@dataclass
class _FakeBudget:
    ok: bool = True

    def can_run(self, role: Any) -> tuple[bool, str]:
        return (self.ok, "" if self.ok else "budget exhausted")


@dataclass
class _FakeRunRecord:
    role: str


@pytest.fixture
def idempotency_store(tmp_path: Path) -> IdempotencyStore:
    return IdempotencyStore(tmp_path / "idempotency.sqlite3")


_ROLES = {"pr-remediator": object(), "queue-shepherd": object(), "night-watch": object()}


def _make_runner(tmp_path, store, holds=None, budget=None):  # type: ignore[no-untyped-def]
    submitted: list[Any] = []
    runner = WatchTickRunner(
        holds=holds or _FakeHolds(),
        budget=budget or _FakeBudget(),
        roles=_ROLES,
        active_runs=lambda: [],
        submit=submitted.append,
        idempotency=store,
        ledger_path=tmp_path / "ledger.jsonl",
    )
    return runner, submitted


@pytest.mark.integration
def test_no_change_between_ticks_makes_zero_dispatches(tmp_path: Path, idempotency_store: IdempotencyStore) -> None:
    runner, submitted = _make_runner(tmp_path, idempotency_store)
    snap = {REPO: _snap(queued=1, age=60)}
    runner.run(snap)
    assert runner.run(snap) == [] and submitted == []
    assert len((tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()) == 2


@pytest.mark.integration
def test_repeated_failed_head_dispatches_exactly_once_per_hour(tmp_path: Path, idempotency_store) -> None:  # type: ignore[no-untyped-def]
    runner, submitted = _make_runner(tmp_path, idempotency_store)
    snap = {REPO: _snap(failed=("deadbeef",))}
    runner.run(snap)  # first sighting: nothing to compare against yet
    dispatched_1 = runner.run(snap)  # second tick: still failing -> escalate
    dispatched_2 = runner.run(snap)  # third tick: same hour -> idempotent no-op
    assert [e.trigger for e in dispatched_1] == ["merge_queue_failed_head"]
    assert dispatched_2 == [] and len(submitted) == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    ("holds", "budget", "active_runs"),
    [
        (_FakeHolds(blocked={("pr-remediator", REPO): "hold rd-2657: RD deploy/release hold"}), _FakeBudget(), []),
        (_FakeHolds(), _FakeBudget(ok=False), []),
        (_FakeHolds(), _FakeBudget(), [_FakeRunRecord(role="pr-remediator")]),
    ],
    ids=["schedule_hold_or_hold_2657", "budget_exhausted", "active_run_in_progress"],
)
def test_gate_blocks_dispatch(tmp_path: Path, idempotency_store, holds, budget, active_runs) -> None:  # type: ignore[no-untyped-def]
    runner, submitted = _make_runner(tmp_path, idempotency_store, holds=holds, budget=budget)
    runner.active_runs = lambda: active_runs
    runner.run({REPO: _snap(failed=("deadbeef",))})
    assert runner.run({REPO: _snap(failed=("deadbeef",))}) == [] and submitted == []


@pytest.mark.integration
def test_rate_limit_backoff_skips_the_whole_tick(tmp_path: Path, idempotency_store: IdempotencyStore) -> None:
    runner, submitted = _make_runner(tmp_path, idempotency_store)
    runner.record_rate_limit(time.time() + 3600)
    runner.run({REPO: _snap(failed=("deadbeef",))})
    assert runner.run({REPO: _snap(failed=("deadbeef",))}) == [] and submitted == []
