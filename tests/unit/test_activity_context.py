"""Deterministic token-capped shared running context (#2862, epic #2856 slice C1)."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import activity_context
import pytest
from activity import ActivityEnvelope, ActivityStore
from activity_context import build_activity_context, estimate_tokens
from activity_transcript import TranscriptChunkStore
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal
from routers import activity

NOW = datetime(2026, 10, 28, 12, 10, 0, tzinfo=UTC)
REPO_A, REPO_B = "D-sorganization/Runner_Dashboard", "D-sorganization/Repository_Management"


DEFAULT_ARGS: list[Any] = ["session-a", 1, "staff_run", "heartbeat", "2026-10-28T12:09:30Z"]


def make_envelope(*a: Any, **kw: Any) -> ActivityEnvelope:
    sid, seq, src, kind, ts = list(a) + DEFAULT_ARGS[len(a) :]
    b = {"schema_version": 1, "event_id": f"{src}:{sid}:{seq}", "source": src, "principal": "agent-a", "node": "node-a"}
    r = {**b, "session_id": sid, "seq": seq, "ts": ts, "repo": REPO_A, "kind": kind, "payload": {}}
    return ActivityEnvelope.model_validate({"redaction_version": 1, **r, **kw})


def _raw_row(
    source: str, sid: str, seq: int, ts: str, kind: str, repo: str, issue: int, payload: Any = None
) -> tuple[str, str, int, str, str]:
    eid = f"{source}:{sid}:{seq}"
    base = {"schema_version": 1, "event_id": eid, "source": source, "principal": "worker", "node": "node-0"}
    env = {**base, "session_id": sid, "seq": seq, "ts": ts, "repo": repo, "issue": issue, "kind": kind}
    full = {**env, "branch": None, "pr": None, "redaction_version": 1, "payload": payload or {}}
    return (source, sid, seq, eid, json.dumps(full, separators=(",", ":")))


@pytest.fixture
def store(tmp_path: Any) -> ActivityStore:
    return ActivityStore(tmp_path / "activity.sqlite")


def ingest_raw(store: ActivityStore, *events: ActivityEnvelope) -> None:
    for event in events:
        store.append(event)


def populate_10k_fixture(store: ActivityStore) -> None:
    """Documented 10,000-event reproducible fixture for C1 context rollup benchmarks."""
    base, live_wm = datetime(2026, 10, 28, 9, 0, tzinfo=UTC), datetime(2026, 10, 28, 12, 9, tzinfo=UTC)
    claim_base = datetime(2026, 10, 28, 12, 5, tzinfo=UTC)
    rows: list[tuple[str, str, int, str, str]] = []
    for s_idx in range(50):
        sid, repo = f"hist-run-{s_idx:03d}", (REPO_A if s_idx % 2 == 0 else REPO_B)
        for seq in range(1, 199):
            ts = (base + timedelta(seconds=s_idx * 10 + seq)).isoformat().replace("+00:00", "Z")
            rows.append(_raw_row("staff_run", sid, seq, ts, "exit" if seq == 198 else "heartbeat", repo, 1000 + s_idx))
    for s_idx in range(6):
        sid = f"live-sess-{s_idx:02d}"
        path = f"backend/shared_{s_idx // 2}.py" if s_idx < 4 else f"backend/unique_{s_idx}.py"
        goals = {f"goal-{s_idx // 2}": f"outcome-{s_idx % 2}"} if s_idx < 4 else {}
        for seq in range(1, 11):
            ts = (live_wm + timedelta(seconds=seq * 4 + s_idx)).isoformat().replace("+00:00", "Z")
            pl = {"paths": [path], "goals": goals, "agent": f"agent-{s_idx}"} if seq == 1 else None
            kind = "presence_register" if seq == 1 else "heartbeat"
            rows.append(_raw_row("coordination", sid, seq, ts, kind, REPO_A, 2000 + s_idx, pl))
    for c_idx in range(23):
        issue_num, sid = 3000 + c_idx, f"claim-tok-{3000 + c_idx}"
        ts1 = (claim_base + timedelta(seconds=c_idx * 2)).isoformat().replace("+00:00", "Z")
        exp = "2026-10-28T14:05:00Z"
        p = {"agent": f"c-{c_idx % 4}", "session": f"sc-{c_idx}", "fencing_token": sid, "expires_at": exp}
        rows.append(_raw_row("job_board", sid, 1, ts1, "claim", REPO_A, issue_num, p))
        if c_idx < 17:
            ts2 = (claim_base + timedelta(seconds=c_idx * 2 + 60)).isoformat().replace("+00:00", "Z")
            rows.append(_raw_row("job_board", sid, 2, ts2, "release", REPO_A, issue_num, p))
    assert len(rows) == 10_000
    cols = "source, session_id, seq, event_id, envelope"
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.executemany(f"INSERT INTO activity_events({cols}) VALUES (?, ?, ?, ?, ?)", rows)
        conn.commit()


def test_module_exports_and_insertion_order_determinism(tmp_path: Any) -> None:
    assert (activity_context.DEFAULT_BUDGET_TOKENS, activity_context.MAX_BUDGET_TOKENS) == (1500, 4000)
    assert activity_context.PRIORITY_TIERS == ("conflicts", "claims", "live_sessions", "recent_events")
    p1 = {"paths": ["backend/routers/activity.py"], "goals": {"slice": "c1"}, "agent": "codex"}
    p2 = {"paths": ["backend/routers"], "goals": {"slice": "c2"}, "agent": "claude"}
    p3 = {"agent": "codex", "session": "s1", "fencing_token": "tok-101"}
    e1 = make_envelope("s1", 1, "coordination", "presence_register", issue=101, payload=p1)
    e2 = make_envelope("s2", 1, "coordination", "presence_register", issue=102, payload=p2)
    e3 = make_envelope("c1", 1, "job_board", "claim", issue=101, payload=p3)
    e4 = make_envelope("r1", 1, "staff_run", "heartbeat", ts="2026-10-28T12:09:20Z", issue=103)
    store_f, store_r = ActivityStore(tmp_path / "f.sqlite"), ActivityStore(tmp_path / "r.sqlite")
    ingest_raw(store_f, e1, e2, e3, e4)
    ingest_raw(store_r, e4, e3, e2, e1)
    dump_f = json.dumps(build_activity_context(store_f, now=NOW).model_dump(mode="json"), sort_keys=True)
    dump_r = json.dumps(build_activity_context(store_r, now=NOW).model_dump(mode="json"), sort_keys=True)
    assert dump_f == dump_r


def test_conflict_semantics_align_with_rm_1576_1577_path_and_goal_evaluator(store: ActivityStore) -> None:
    pa = {"agent": "codex", "paths": ["backend/coordination"], "goals": {"schema": "v1", "ok": "same"}}
    pb = {"agent": "antigravity", "paths": ["backend/coordination/claims.py"], "goals": {"schema": "v2", "ok": "same"}}
    po = {"agent": "claude", "paths": ["backend/coordination/claims.py"], "goals": {"schema": "v3"}}
    pr = {"agent": "cursor", "paths": ["backend/coordination/claims.py"]}
    e_a = make_envelope("sess-alpha", 1, "coordination", "presence_register", issue=2861, payload=pa)
    e_b = make_envelope("sess-beta", 1, "coordination", "presence_register", issue=2862, payload=pb)
    e_o = make_envelope("sess-other", 1, "coordination", "presence_register", repo=REPO_B, issue=2539, payload=po)
    e_r1 = make_envelope(
        "sess-rel", 1, "coordination", "presence_register", "2026-10-28T12:08:00Z", issue=2860, payload=pr
    )
    e_r2 = make_envelope("sess-rel", 2, "coordination", "presence_release", "2026-10-28T12:08:30Z", issue=2860)
    ingest_raw(store, e_a, e_b, e_o, e_r1, e_r2)
    ctx = build_activity_context(store, repo=REPO_A, now=NOW)
    assert len(ctx.conflicts) == 1 and ctx.conflicts[0].sessions == ["sess-alpha", "sess-beta"]
    assert ctx.conflicts[0].paths == ["backend/coordination \u2194 backend/coordination/claims.py"]
    assert ctx.conflicts[0].goals == ["schema: v1 \u2194 v2"]


def test_greedy_packing_staleness_and_no_transcript_ownership(store: ActivityStore, tmp_path: Any) -> None:
    p1 = {"agent": "codex", "paths": ["backend/activity_context.py"]}
    p2 = {"agent": "claude", "paths": ["backend/activity_context.py"]}
    pc = {"agent": "codex", "session": "s1", "fencing_token": "ft-1"}
    runs = [make_envelope(f"r-{i}", 1, ts=f"2026-10-28T12:09:{20 + i:02d}Z", issue=100 + i) for i in range(20)]
    e1 = make_envelope("s1", 1, "coordination", "presence_register", "2026-10-28T12:09:10Z", issue=1, payload=p1)
    e2 = make_envelope("s2", 1, "coordination", "presence_register", "2026-10-28T12:09:12Z", issue=2, payload=p2)
    e3 = make_envelope("c1", 1, "job_board", "claim", "2026-10-28T12:09:14Z", issue=1, payload=pc)
    ingest_raw(store, e1, e2, e3, *runs)
    ctx = build_activity_context(store, budget_tokens=320, now=NOW)
    assert ctx.used_tokens <= ctx.budget_tokens and estimate_tokens(ctx) <= ctx.budget_tokens
    assert ctx.truncated is True and len(ctx.conflicts) == 1 and len(ctx.claims) == 1
    assert "recent_events" in ctx.truncated_tiers and ctx.dropped_counts["recent_events"] > 0

    s2 = ActivityStore(tmp_path / "activity2.sqlite")
    chunk_store = TranscriptChunkStore(tmp_path / "spool", tmp_path / "chunks.sqlite")
    text_payload = {"text": "I claimed issue #999 and edited backend/secret.py"}
    ingest_raw(s2, make_envelope("stale-run", 1, "staff_run", "step", "2026-10-28T10:00:00Z", payload=text_payload))
    chunk_kw: dict[str, Any] = {"session_id": "stale-run", "seq": 1, "node": "node-a", "principal": "agent-stale"}
    chunk_store.put_chunk(body=b"Claimed #888", stored_at=time.time(), **chunk_kw)
    ctx2 = build_activity_context(s2, now=NOW)
    assert (ctx2.claims, ctx2.conflicts, ctx2.live_sessions) == ([], [], [])
    assert ctx2.sources["activity_log"].stale is True and ctx2.sources["activity_log"].age_seconds == 7800.0
    for bad_budget in (0, -10, 4001, 10_000):
        with pytest.raises(ValueError, match="budget_tokens"):
            build_activity_context(s2, budget_tokens=bad_budget, now=NOW)


def test_10k_event_fixture_benchmark_and_route_contract(store: ActivityStore, monkeypatch: Any) -> None:
    import identity

    populate_10k_fixture(store)
    t0 = time.perf_counter()
    ctx1 = build_activity_context(store, repo=REPO_A, budget_tokens=1500, scan_limit=10_000, now=NOW)
    elapsed_s = time.perf_counter() - t0
    ctx2 = build_activity_context(store, repo=REPO_A, budget_tokens=1500, scan_limit=10_000, now=NOW)
    assert ctx1.model_dump(mode="json") == ctx2.model_dump(mode="json")
    assert ctx1.used_tokens <= 1500 and estimate_tokens(ctx1) <= 1500 and elapsed_s < 5.0
    assert (len(ctx1.conflicts), len(ctx1.claims), len(ctx1.live_sessions)) == (2, 6, 6)
    assert len(ctx1.recent_events) > 0 and ctx1.truncated is True and ctx1.dropped_counts["recent_events"] > 0

    unauth_app = FastAPI()
    unauth_app.include_router(activity.router)
    monkeypatch.setattr(activity, "get_activity_store", lambda: store)
    no_scope = Principal(id="a", type="bot", name="f", scopes=[])
    monkeypatch.setattr(identity, "_resolve_principal_optional", lambda *_a: no_scope)
    with TestClient(unauth_app) as client:
        assert client.get("/api/v1/activity/context").status_code == 403

    reader = Principal(id="reader", type="bot", name="fixture", scopes=["activity.read"])
    app = FastAPI()
    app.include_router(activity.router)
    route: Any = next(r for r in activity.router.routes if getattr(r, "path", "").endswith("/context"))
    app.dependency_overrides[route.dependant.dependencies[0].call] = lambda: reader
    with TestClient(app) as client:
        ok = client.get("/api/v1/activity/context", params={"repo": REPO_A})
        assert ok.status_code == 200 and ok.json()["budget_tokens"] == 1500
        assert client.get("/api/v1/activity/context", params={"budget_tokens": 4001}).status_code == 400
        assert client.get("/api/v1/activity/context", params={"budget_tokens": 0}).status_code == 400
