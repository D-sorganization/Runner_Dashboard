"""TDD regression tests for LanePromptStore.health_summary and GET /api/coordination/lane-prompts/health."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_health_summary_classifies_healthy_stalled_expired_and_idle(tmp_path: Path) -> None:
    from coordination.lane_prompts import LanePromptStore

    store = LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    base = 1_700_000_000.0

    for lid in ("lane-healthy", "lane-hb-stale", "lane-prog-stale", "lane-expired", "lane-idle"):
        store.register_assignment(
            lane_id=lid,
            role="backlog-completion",
            repos=["Runner_Dashboard"],
            release_sha="abc123",
            package_hash="pkg123",
            subagent_model="",
            max_children=0,
            now=base,
        )

    c_healthy = store.checkout(
        session_id="codex-1",
        principal="loopback-dev",
        idempotency_key="k1",
        lane_id="lane-healthy",
        ttl_sec=3600,
        now=base,
    )
    store.heartbeat(
        checkout_id=c_healthy["checkout_id"],
        fence_token=c_healthy["fence_token"],
        session_id="codex-1",
        progress=True,
        now=base + 1200,
    )

    c_hb = store.checkout(
        session_id="codex-2",
        principal="loopback-dev",
        idempotency_key="k2",
        lane_id="lane-hb-stale",
        ttl_sec=3600,
        now=base,
    )
    # No heartbeat for 1000s (>= 900s) -> stalled_heartbeat
    _ = c_hb

    c_prog = store.checkout(
        session_id="codex-3",
        principal="loopback-dev",
        idempotency_key="k3",
        lane_id="lane-prog-stale",
        ttl_sec=3600,
        now=base,
    )
    # Heartbeat fresh at +1200, but progress=False since base (1850s >= 1800s) -> stalled_progress
    store.heartbeat(
        checkout_id=c_prog["checkout_id"],
        fence_token=c_prog["fence_token"],
        session_id="codex-3",
        progress=False,
        now=base + 1850,
    )

    store.checkout(
        session_id="codex-4",
        principal="loopback-dev",
        idempotency_key="k4",
        lane_id="lane-expired",
        ttl_sec=60,
        now=base,
    )

    now = base + 1900.0
    summary = store.health_summary(
        stall_heartbeat_sec=900,
        stall_progress_sec=1800,
        now=now,
    )
    assert summary["needs_attention"] is True
    assert [x["lane_id"] for x in summary["healthy_active"]] == ["lane-healthy"]
    assert [x["lane_id"] for x in summary["stalled_heartbeat"]] == ["lane-hb-stale"]
    assert [x["lane_id"] for x in summary["stalled_progress"]] == ["lane-prog-stale"]
    assert [x["lane_id"] for x in summary["expired_recent"]] == ["lane-expired"]
    # lane-expired becomes unassigned/idle once expired, alongside lane-idle
    assert {x["lane_id"] for x in summary["idle_lanes"]} == {"lane-idle", "lane-expired"}


@pytest.mark.requires_posix
def test_health_endpoint_returns_summary(tmp_path: Path, monkeypatch) -> None:
    from coordination import lane_prompts as lane_prompts_mod
    from fastapi import FastAPI
    from routers import coordination as coordination_router

    store = lane_prompts_mod.LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    store.register_assignment(
        lane_id="lane-idle-1",
        role="backlog-completion",
        repos=["Runner_Dashboard"],
        release_sha="abc123",
        package_hash="pkg123",
        subagent_model="",
        max_children=0,
    )
    monkeypatch.setattr(lane_prompts_mod, "get_lane_prompt_store", lambda: store)

    test_app = FastAPI()
    test_app.include_router(coordination_router.router)
    test_app.dependency_overrides[coordination_router.require_fleet_peer] = lambda: "test-peer"

    client = TestClient(test_app)
    resp = client.get("/api/coordination/lane-prompts/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["needs_attention"] is True
    assert [x["lane_id"] for x in body["idle_lanes"]] == ["lane-idle-1"]


def test_recovered_and_disabled_lanes_clear_expired_attention(tmp_path: Path) -> None:
    """Recovered or disabled lanes must not keep needs_attention=True from historical expirations."""
    from coordination.lane_prompts import LanePromptStore

    store = LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    base = 1_700_000_000.0
    store.register_assignment(
        lane_id="lane-recover",
        role="backlog-completion",
        repos=["Runner_Dashboard"],
        release_sha="abc123",
        package_hash="pkg123",
        subagent_model="",
        max_children=0,
        now=base,
    )
    # Checkout expires at base + 60.
    store.checkout(
        session_id="codex-old",
        principal="loopback-dev",
        idempotency_key="k-old",
        lane_id="lane-recover",
        ttl_sec=60,
        now=base,
    )
    # Next day, a fresh healthy checkout replaces it.
    day1 = base + 86400
    fresh = store.checkout(
        session_id="codex-new",
        principal="loopback-dev",
        idempotency_key="k-new",
        lane_id="lane-recover",
        ttl_sec=3600,
        now=day1,
    )
    summary_recovered = store.health_summary(now=day1 + 100)
    assert [x["lane_id"] for x in summary_recovered["healthy_active"]] == ["lane-recover"]
    assert summary_recovered["expired_recent"] == []
    assert summary_recovered["idle_lanes"] == []
    assert summary_recovered["needs_attention"] is False

    # Release and disable the lane: day-30 check must stay clean.
    store.release(
        checkout_id=fresh["checkout_id"],
        fence_token=fresh["fence_token"],
        session_id="codex-new",
        reason="done",
        now=day1 + 200,
    )
    store.register_assignment(
        lane_id="lane-recover",
        role="backlog-completion",
        repos=["Runner_Dashboard"],
        release_sha="abc123",
        package_hash="pkg123",
        subagent_model="",
        max_children=0,
        enabled=False,
        now=day1 + 300,
    )
    day30 = base + 30 * 86400
    summary_disabled = store.health_summary(now=day30)
    assert summary_disabled["expired_recent"] == []
    assert summary_disabled["idle_lanes"] == []
    assert summary_disabled["needs_attention"] is False
