"""Tests for durable lane-prompt checkout, fencing, idempotency, and heartbeat."""

from __future__ import annotations

import concurrent.futures
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _make_store(tmp_path: Path):
    from coordination.lane_prompts import LanePromptStore

    store = LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    store.register_assignment(
        lane_id="codex-desk-1",
        role="backlog-completion",
        repos=["Runner_Dashboard", "Repository_Management"],
        release_sha="949ede4d",
        package_hash="abc123",
        subagent_model="gpt-6-luna",
        max_children=2,
        task_focus="Clear stranded CLEAN PRs.",
    )
    return store


def test_concurrent_checkout_single_winner_and_idempotency_survives_restart(
    tmp_path: Path,
) -> None:
    """Concurrent checkouts yield 1 winner and 409 for loser; retries survive restart."""
    from coordination.lane_prompts import LaneConflictError, LanePromptStore

    store = _make_store(tmp_path)

    def _try_claim(idx: int):
        return store.checkout(
            session_id=f"sess-{idx}",
            principal="agent-codex",
            idempotency_key=f"idem-{idx}",
            lane_id="codex-desk-1",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(_try_claim, 1), pool.submit(_try_claim, 2)]
        winners = []
        conflicts = []
        for fut in futs:
            try:
                winners.append(fut.result())
            except LaneConflictError as exc:
                conflicts.append(exc)

    assert len(winners) == 1
    assert len(conflicts) == 1
    win = winners[0]
    assert win["fence_token"] == 1
    assert win["lane_id"] == "codex-desk-1"

    # Simulate process restart with a fresh store instance on the same SQLite DB.
    restarted = LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    retry = restarted.checkout(
        session_id=win["session_id"],
        principal="agent-codex",
        idempotency_key=win["idempotency_key"],
        lane_id="codex-desk-1",
    )
    assert retry["checkout_id"] == win["checkout_id"]
    assert retry["fence_token"] == win["fence_token"]

    # No-arg checkout resumes the active session's checkout.
    resumed = restarted.checkout(
        session_id=win["session_id"],
        principal="agent-codex",
        idempotency_key="idem-resume",
    )
    assert resumed["checkout_id"] == win["checkout_id"]


def test_heartbeat_stale_fence_fails_and_expiry_allows_reclaim(tmp_path: Path) -> None:
    """Stale fence fails with 409; expired checkout can be reclaimed with higher fence."""
    from coordination.lane_prompts import StaleLaneFenceError

    store = _make_store(tmp_path)
    now = 1000.0
    c1 = store.checkout(
        session_id="sess-a",
        principal="agent-codex",
        idempotency_key="idem-a",
        lane_id="codex-desk-1",
        ttl_sec=60,
        now=now,
    )
    assert c1["fence_token"] == 1

    # Heartbeat without progress keeps progress_at unchanged.
    hb = store.heartbeat(
        checkout_id=c1["checkout_id"],
        fence_token=1,
        session_id="sess-a",
        ttl_sec=60,
        progress=False,
        now=1030.0,
    )
    assert hb["heartbeat_at"] == 1030.0
    assert hb["progress_at"] is None

    # Wrong fence fails.
    with pytest.raises(StaleLaneFenceError):
        store.heartbeat(
            checkout_id=c1["checkout_id"],
            fence_token=99,
            session_id="sess-a",
            now=1035.0,
        )

    # After expiry (1030 + 60 = 1090), another session can reclaim with fence_token=2.
    c2 = store.checkout(
        session_id="sess-b",
        principal="agent-codex",
        idempotency_key="idem-b",
        role="backlog-completion",
        ttl_sec=60,
        now=1100.0,
    )
    assert c2["fence_token"] == 2
    assert c2["session_id"] == "sess-b"

    # Old holder cannot release with stale fence.
    with pytest.raises(StaleLaneFenceError):
        store.release(
            checkout_id=c1["checkout_id"],
            fence_token=1,
            session_id="sess-a",
            now=1105.0,
        )

    rel = store.release(
        checkout_id=c2["checkout_id"],
        fence_token=2,
        session_id="sess-b",
        now=1110.0,
    )
    assert rel["status"] == "released"
