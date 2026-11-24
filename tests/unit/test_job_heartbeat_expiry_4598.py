"""Late heartbeats must not revive an expired job lease, even with stale RM evidence."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from unittest.mock import Mock

import pytest
from coordination import claim_state, claims, job_board_lease, jobs
from coordination.job_board_rows import JobLeaseRow

NOW = datetime(2026, 11, 22, tzinfo=UTC)
REPO, ISSUE, AGENT, SESSION = "Runner_Dashboard", 4598, "codex", "epoch-one"


class Clock(datetime):
    current = NOW

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return cls.current


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[claim_state.ClaimStateStore]:
    database = claim_state.ClaimStateStore(tmp_path / "claims.sqlite3")
    monkeypatch.setattr(claim_state, "get_store", lambda: database)
    monkeypatch.setattr(job_board_lease, "datetime", Clock)
    monkeypatch.setattr(Clock, "current", NOW)
    yield database
    database.close()


def seed(store: claim_state.ClaimStateStore, seconds_remaining: float) -> JobLeaseRow:
    fence = store.mint_fence(REPO, ISSUE)
    row = JobLeaseRow(REPO, ISSUE, AGENT, SESSION, fence, NOW.timestamp() + seconds_remaining, 2)
    store.put_job_lease(row)
    return row


def remote_claim(monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, Mock]:
    check = Mock(return_value={"available": True, "held": True, "agent": AGENT, "session": SESSION})
    post = Mock(return_value={"result": {"expires_at": (NOW + timedelta(hours=2)).isoformat()}, "warnings": []})
    monkeypatch.setattr(claims, "check", check)
    monkeypatch.setattr(claims, "post_lease", post)
    return check, post


def beat(fence: int) -> dict[str, object]:
    return job_board_lease.heartbeat_job(REPO, ISSUE, agent=AGENT, session=SESSION, fence=fence)


@pytest.mark.parametrize("seconds_remaining", [0, -1, -3600])
def test_expired_heartbeat_never_reads_or_posts_remote_claim(
    store: claim_state.ClaimStateStore, monkeypatch: pytest.MonkeyPatch, seconds_remaining: float
) -> None:
    row = seed(store, seconds_remaining)
    check, post = remote_claim(monkeypatch)

    with pytest.raises(jobs.StaleFenceError, match="expired"):
        beat(row.fence)

    check.assert_not_called()
    post.assert_not_called()
    assert store.get_job_lease(REPO, ISSUE) == row


def test_live_heartbeat_renews_same_epoch(store: claim_state.ClaimStateStore, monkeypatch: pytest.MonkeyPatch) -> None:
    row = seed(store, 1)
    _, post = remote_claim(monkeypatch)

    assert beat(row.fence)["heartbeated"] is True

    post.assert_called_once()
    renewed = store.get_job_lease(REPO, ISSUE)
    assert renewed is not None and renewed.fence == row.fence
    assert renewed.expires_at == (NOW + timedelta(hours=2)).timestamp()


def test_forgotten_lease_cannot_be_recreated_by_heartbeat(
    store: claim_state.ClaimStateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = seed(store, -1)
    store.forget_job_lease(row)
    check, post = remote_claim(monkeypatch)

    with pytest.raises(jobs.StaleFenceError):
        beat(row.fence)

    check.assert_not_called()
    post.assert_not_called()
    assert store.get_job_lease(REPO, ISSUE) is None


def test_fence_is_checked_after_entering_issue_lock(
    store: claim_state.ClaimStateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = seed(store, 60)
    check, post = remote_claim(monkeypatch)

    @contextmanager
    def takeover_before_lock(repo: str, issue: int) -> Iterator[None]:
        store.mint_fence(repo, issue)
        yield

    monkeypatch.setattr(claims, "hold_issue", takeover_before_lock)
    with pytest.raises(jobs.StaleFenceError):
        beat(row.fence)

    check.assert_not_called()
    post.assert_not_called()
    assert store.get_job_lease(REPO, ISSUE) == row


@pytest.mark.parametrize("changed", [{"agent": "claude"}, {"session": "epoch-two"}, {"fence": 99}])
def test_durable_owner_must_match_even_when_remote_claim_is_stale(
    store: claim_state.ClaimStateStore, monkeypatch: pytest.MonkeyPatch, changed: dict[str, object]
) -> None:
    row = seed(store, 60)
    replacement = replace(row, **changed)
    store.put_job_lease(replacement)
    check, post = remote_claim(monkeypatch)

    with pytest.raises(jobs.StaleFenceError):
        beat(row.fence)

    check.assert_not_called()
    post.assert_not_called()
    assert store.get_job_lease(REPO, ISSUE) == replacement


@pytest.mark.parametrize("change", ["expiry", "takeover"])
def test_remote_lookup_cannot_renew_a_lease_that_became_stale(
    store: claim_state.ClaimStateStore, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    row = seed(store, 1)
    check, post = remote_claim(monkeypatch)

    def stale_remote_reply(*args: object) -> dict[str, object]:
        if change == "expiry":
            Clock.current = NOW + timedelta(seconds=1)
        else:
            store.mint_fence(REPO, ISSUE)
        return {"available": True, "held": True, "agent": AGENT, "session": SESSION}

    check.side_effect = stale_remote_reply
    with pytest.raises(jobs.StaleFenceError):
        beat(row.fence)

    post.assert_not_called()
    assert store.get_job_lease(REPO, ISSUE) == row
