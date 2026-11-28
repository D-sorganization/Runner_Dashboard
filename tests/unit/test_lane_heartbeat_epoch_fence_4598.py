"""Epoch-fenced heartbeat TTL expiry for lane holds (#4598).

A delayed heartbeat must not revive an expired lane checkout. The heartbeat
path fences renewal on TTL expiry (``expires_at <= now``) — the same expiry
predicate the read paths use — so a checkout whose TTL elapsed rejects the
renewal instead of minting a fresh lease on a dead owner.

The 900s stale-heartbeat window (#4677) is a reclaim-side accelerator, not a
renewal fence: a live-but-slow holder heartbeating inside its TTL still renews.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _ROOT / "backend"
for _p in (str(_ROOT), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from coordination import lane_prompts  # noqa: E402

BASE = 10_000.0


def _store(tmp_path: Path) -> lane_prompts.LanePromptStore:
    store = lane_prompts.LanePromptStore(db_path=tmp_path / "lane.sqlite3")
    store.register_assignment(
        lane_id="lane-deepseek",
        role="backlog-completion",
        repos=["Runner_Dashboard"],
        release_sha="sha",
        package_hash="pkg",
        subagent_model="",
        max_children=0,
        now=BASE,
    )
    return store


def _checkout(store: lane_prompts.LanePromptStore, *, session: str, key: str, ttl: int, now: float) -> dict:
    return store.checkout(
        session_id=session,
        principal="agent-deepseek",
        idempotency_key=key,
        lane_id="lane-deepseek",
        ttl_sec=ttl,
        now=now,
    )


def _row(store: lane_prompts.LanePromptStore, checkout_id: str):
    with store._connect() as conn:
        return conn.execute(
            "SELECT status, expires_at, heartbeat_at, fence_token FROM lane_checkouts WHERE checkout_id = ?",
            (checkout_id,),
        ).fetchone()


def test_heartbeat_after_ttl_expiry_is_rejected_and_does_not_renew(tmp_path: Path) -> None:
    store = _store(tmp_path)
    held = _checkout(store, session="deepseek-dead", key="k-ttl", ttl=60, now=BASE)
    assert held["expires_at"] == BASE + 60

    # TTL elapsed: a delayed heartbeat must be fenced, not treated as a renewal.
    with pytest.raises(lane_prompts.StaleLaneFenceError):
        store.heartbeat(
            checkout_id=held["checkout_id"],
            fence_token=held["fence_token"],
            session_id="deepseek-dead",
            now=BASE + 61,
        )

    row = _row(store, held["checkout_id"])
    assert row["expires_at"] == BASE + 60
    assert row["heartbeat_at"] == BASE


def test_heartbeat_within_ttl_renews_even_past_stale_window(tmp_path: Path) -> None:
    store = _store(tmp_path)
    held = _checkout(store, session="deepseek-live", key="k-slow", ttl=3600, now=BASE)
    # 901s gap exceeds the 900s stale-heartbeat window, but the TTL is still
    # ahead: the renewal fence is TTL-only, so a live-but-slow holder renews.
    renewed = store.heartbeat(
        checkout_id=held["checkout_id"],
        fence_token=held["fence_token"],
        session_id="deepseek-live",
        now=BASE + 901,
    )
    assert renewed["expires_at"] == BASE + 901 + 3600
    row = _row(store, held["checkout_id"])
    assert row["expires_at"] == BASE + 901 + 3600
    assert row["heartbeat_at"] == BASE + 901


def test_expired_checkout_is_reclaimable_after_rejected_heartbeat(tmp_path: Path) -> None:
    store = _store(tmp_path)
    held = _checkout(store, session="deepseek-dead", key="k-reclaim", ttl=60, now=BASE)
    with pytest.raises(lane_prompts.StaleLaneFenceError):
        store.heartbeat(
            checkout_id=held["checkout_id"],
            fence_token=held["fence_token"],
            session_id="deepseek-dead",
            now=BASE + 61,
        )
    nxt = _checkout(store, session="deepseek-relaunch", key="k-relaunch", ttl=3600, now=BASE + 62)
    assert nxt["session_id"] == "deepseek-relaunch"
    assert nxt["fence_token"] == held["fence_token"] + 1
    assert nxt["checkout_id"] != held["checkout_id"]


def test_late_heartbeat_cannot_revive_a_reclaimed_lane(tmp_path: Path) -> None:
    store = _store(tmp_path)
    held = _checkout(store, session="deepseek-dead", key="k-dead", ttl=60, now=BASE)
    _checkout(store, session="deepseek-relaunch", key="k-relaunch", ttl=3600, now=BASE + 61)
    # The dead holder's fence is a previous epoch: the heartbeat must fail and
    # must not touch the new holder's checkout.
    with pytest.raises(lane_prompts.StaleLaneFenceError):
        store.heartbeat(
            checkout_id=held["checkout_id"],
            fence_token=held["fence_token"],
            session_id="deepseek-dead",
            now=BASE + 62,
        )
    row = _row(store, held["checkout_id"])
    assert row["expires_at"] == BASE + 60


def test_live_heartbeat_still_renews_within_window(tmp_path: Path) -> None:
    store = _store(tmp_path)
    held = _checkout(store, session="deepseek-live", key="k-live", ttl=3600, now=BASE)
    renewed = store.heartbeat(
        checkout_id=held["checkout_id"],
        fence_token=held["fence_token"],
        session_id="deepseek-live",
        progress=True,
        now=BASE + 600,
    )
    assert renewed["expires_at"] == BASE + 600 + 3600
    row = _row(store, held["checkout_id"])
    assert row["expires_at"] == BASE + 600 + 3600
    assert row["heartbeat_at"] == BASE + 600
    assert row["fence_token"] == held["fence_token"]
