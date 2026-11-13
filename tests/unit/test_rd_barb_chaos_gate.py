"""rd-barb slice 6 (#3074): chaos gate — dedupe under retry, webhook/heartbeat outage, leader kill & hop limit."""

from __future__ import annotations

import datetime as _dt_mod
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

if not hasattr(_dt_mod, "UTC"):
    _dt_mod.UTC = _dt_mod.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017

from coordination.relay_guard import MAX_HOPS
from fleet_validators import FleetAPIError
from staff.barb_continuity import BarbContinuityStore
from staff.barb_lease_fence import StaleBarbLease
from staff.barb_node_leader import assume_leadership, claim_for
from staff.watch_tick import ETagCache, ProbeResponse

from scripts import barb_cli, rd_threads_envelope
from scripts import rd_barb_notify as rn
from scripts.rd_barb_postman import HEARTBEAT_INTERVAL_S, PostmanLeader

UTC = _dt_mod.UTC
T0 = datetime(2026, 10, 29, 15, 0, 0, tzinfo=UTC)
ROSTER = frozenset({"claude", "codex", "antigravity", "cursor", "rd-barb", "barb"})
WEBHOOK_CFG = {
    "RD_BARB_WEBHOOK_URL": "https://hook.example/rd-barb",
    "RD_BARB_WEBHOOK_KEY": "test-webhook-key",  # pragma: allowlist secret
    "RD_BARB_THREAD_ID": "rd-threads:barb",
}


class FakeHub:
    """In-memory staff threads fake supporting crash-first-attempt and #3024 concurrent race."""

    def __init__(self, *, fail_first_post: bool = False, race: bool = False) -> None:
        self.fail_first_post, self.race = fail_first_post, race
        self.msgs: dict[str, list[dict[str, Any]]] = {}
        self._lock, self._inflight = threading.Lock(), set()  # type: ignore[var-annotated]

    def request(self, method: str, path: str, *, params: Any = None, body: Any = None, headers: Any = None) -> Any:
        if path == barb_cli.THREADS:
            with self._lock:
                self.msgs.setdefault(body["title"], [])
                return {"id": body["title"]}
        tid, key = path.split("/")[5], str((headers or {}).get("Idempotency-Key") or "")
        with self._lock:
            if any(m["idempotency_key"] == key for m in self.msgs.get(tid, [])):
                return {"replay": True}
            if self.fail_first_post:
                self.fail_first_post = False
                raise FleetAPIError(502, {"detail": "transient gateway crash"})
            if self.race and key in self._inflight:
                raise FleetAPIError(500, {"detail": "UNIQUE constraint failed"})
            self._inflight.add(key)
        if self.race:
            time.sleep(0.02)
        with self._lock:
            self.msgs.setdefault(tid, []).append({"idempotency_key": key, **body})
        return {}

    def posted_envelopes(self) -> list[dict[str, Any]]:
        return [m["meta"]["rd_threads"] for rows in self.msgs.values() for m in rows]


class _CrashingMessage(dict[str, Any]):
    def get(self, key: str, default: Any = None) -> Any:
        if key == "id":
            raise RuntimeError("simulated leader kill mid-batch")
        return super().get(key, default)


class FakeStream:
    """Stream fake supporting a mid-batch leader crash at index ``kill_after``."""

    def __init__(self, events: list[dict[str, Any]] | None = None, *, kill_after: int | None = None) -> None:
        self.events, self.kill_after = list(events or []), kill_after

    def drain(self) -> list[dict[str, Any]]:
        if self.kill_after is None:
            out, self.events = list(self.events), []
            return out
        limit, self.kill_after = self.kill_after, None
        return [_CrashingMessage(e) if i == limit else dict(e) for i, e in enumerate(self.events)]


class FakeProbe:
    def __init__(self, responses: list[ProbeResponse] | None = None) -> None:
        self.responses = list(responses or [])

    def get(self, path: str, *, etag: str | None) -> ProbeResponse:
        return self.responses.pop(0) if self.responses else ProbeResponse(status_code=304, etag=etag)


class FakeWebhook:
    def __init__(self, *, down: bool = False) -> None:
        self.down, self.sent = down, []  # type: ignore[var-annotated]

    def __call__(self, msg: dict[str, Any]) -> dict[str, Any]:
        if self.down:
            return {"ok": False, "status": "not_sent", "error": "webhook_unreachable"}
        self.sent.append(dict(msg))
        return {"ok": True, "status": "sent"}

    def http_sender(self, _url: str, _headers: Any, payload: dict[str, Any]) -> None:
        if self.down:
            raise OSError("webhook down")
        self.sent.append(dict(payload))


def _env(mid: str, **over: Any) -> dict[str, Any]:
    base = {
        "id": mid,
        "correlation_id": f"c_{mid[2:]}",
        "kind": "update",
        "from": "claude",
        "to": "rd-barb",
        "summary": f"ping {mid}",
        "body": "hello @rd-barb",
        "urgency": "normal",
        "importance": "normal",
        "ack_required": False,
        "task_state": "completed",
        "hops": 0,
    }
    return {**base, **over}


def _make_leader(tmp_path: Path, **kw: Any) -> PostmanLeader:
    now: datetime = kw.get("now", T0)
    node: str = kw.get("node", "hub-a")
    store: BarbContinuityStore = kw.get("store") or BarbContinuityStore(tmp_path / "barb.db")
    epoch: int = kw.get("epoch") or assume_leadership(store, node=node, now=now)["lease"]["epoch"]
    bp = kw.get("board_posts")
    posts: list[str] = bp if isinstance(bp, list) else []
    return PostmanLeader(
        continuity=store,
        claim=claim_for(node, epoch),
        state_path=kw.get("state_path") or (tmp_path / "postman_leader_state.json"),
        stream=kw.get("stream") or FakeStream(),
        board_probe=kw.get("probe") or FakeProbe(),
        etag_cache=ETagCache(),
        webhook=kw.get("webhook") or FakeWebhook(),
        board_fallback=posts.append,
        roster=ROSTER,
        clock=kw.get("clock") or (lambda: now),
    )


@pytest.mark.unit
def test_dedupe_60s_under_retry(tmp_path: Path) -> None:
    bucket_t0, hub, webhook, state_file = (
        1_800_000.0,
        FakeHub(fail_first_post=True, race=True),
        FakeWebhook(),
        tmp_path / "notify.json",
    )

    def _relay_once(env_msg: dict[str, Any], ts: float) -> dict[str, Any]:
        alert = {
            "kind": "alert",
            "summary": env_msg["summary"],
            "body": env_msg.get("body", ""),
            "urgency": "high",
            "dedupe_key": rd_threads_envelope.dedupe_key(env_msg, ts),
            "recommended_action": "Inspect thread message.",
        }
        return rn.Notifier(WEBHOOK_CFG, state_file, webhook.http_sender, None, clock=lambda: ts).notify(alert)

    # 1) Sender crash (502 on first attempt) + retry within the 60 s bucket -> 1 thread msg & 1 webhook relay.
    sender_a = barb_cli.BarbCli(hub, agent="claude", token="tok", clock=lambda: bucket_t0, sleep=lambda _s: None)
    sender_b = barb_cli.BarbCli(hub, agent="claude", token="tok", clock=lambda: bucket_t0 + 15.0, sleep=lambda _s: None)
    r_crash_retry = sender_a.send(to="barb", kind="update", summary="deploy check")
    r_second = sender_b.send(to="barb", kind="update", summary="deploy check")
    assert r_crash_retry["ok"] is True and r_second["status"] == "replayed"
    assert r_crash_retry["correlation_id"] == r_second["correlation_id"]
    first_env = hub.posted_envelopes()[0]
    assert _relay_once(first_env, bucket_t0)["status"] == "sent"
    assert _relay_once(first_env, bucket_t0 + 15.0)["status"] == "deduped"
    assert len(hub.posted_envelopes()) == 1 and len(webhook.sent) == 1

    # 2) Concurrent duplicate sends (#3024 shape) within the 60 s bucket -> 1 thread msg & 1 webhook relay.
    def _concurrent_send(_i: int) -> dict[str, Any]:
        return barb_cli.BarbCli(hub, agent="codex", token="tok", clock=lambda: bucket_t0 + 20.0, sleep=time.sleep).send(
            to="barb", kind="update", summary="concurrent ping"
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert [r["ok"] for r in pool.map(_concurrent_send, range(2))] == [True, True]
    conc_envs = [e for e in hub.posted_envelopes() if e["summary"] == "concurrent ping"]
    assert len(conc_envs) == 1
    assert _relay_once(conc_envs[0], bucket_t0 + 20.0)["status"] == "sent"
    assert _relay_once(conc_envs[0], bucket_t0 + 25.0)["status"] == "deduped"
    assert len(webhook.sent) == 2

    # 3) Leader failover mid-delivery produces 1 thread message and 1 Grok webhook relay.
    store = BarbContinuityStore(tmp_path / "barb.db")
    acq1 = assume_leadership(store, node="hub-a", ttl_seconds=60, now=T0)
    failover_msg = rd_threads_envelope.seal(
        {"kind": "update", "from": "claude", "to": "rd-barb", "summary": "failover item", "hops": 0}, bucket_t0 + 30.0
    )
    t_a = _make_leader(
        tmp_path, node="hub-a", epoch=acq1["lease"]["epoch"], store=store, stream=FakeStream([failover_msg]), now=T0
    ).tick()
    assert len(t_a.delivered) == 1 and _relay_once(t_a.delivered[0], bucket_t0 + 30.0)["status"] == "sent"
    lapsed = T0 + timedelta(seconds=120)
    promoted = assume_leadership(store, node="hub-b", force=True, now=lapsed)
    t_b = _make_leader(
        tmp_path,
        node="hub-b",
        epoch=promoted["lease"]["epoch"],
        store=store,
        stream=FakeStream([failover_msg]),
        now=lapsed,
    ).tick()
    assert (
        t_b.delivered == ()
        and _relay_once(failover_msg, bucket_t0 + 40.0)["status"] == "deduped"
        and len(webhook.sent) == 3
    )

    # 4) A distinct message in the next 60 s bucket is delivered and relayed cleanly.
    next_bucket = bucket_t0 + rd_threads_envelope.DEDUPE_BUCKET_S + 5.0
    r_next = barb_cli.BarbCli(hub, agent="claude", token="tok", clock=lambda: next_bucket, sleep=lambda _s: None).send(
        to="barb", kind="update", summary="deploy check"
    )
    assert (
        r_next["ok"] is True
        and r_next["status"] == "sent"
        and r_next["correlation_id"] != r_crash_retry["correlation_id"]
    )
    next_env = [e for e in hub.posted_envelopes() if e["correlation_id"] == r_next["correlation_id"]][0]
    assert _relay_once(next_env, next_bucket)["status"] == "sent" and len(webhook.sent) == 4


@pytest.mark.unit
def test_webhook_heartbeat_shared_failure(tmp_path: Path) -> None:
    webhook = FakeWebhook(down=True)
    board_posts: list[str] = []
    now = T0
    leader = _make_leader(tmp_path, webhook=webhook, board_posts=board_posts, clock=lambda: now)
    assert leader.tick().heartbeat_sent is False
    alert_1 = {"id": "a_1", "dedupe_key": "alert-1", "kind": "alert", "summary": "runner disk high"}
    alert_2 = {"id": "a_2", "dedupe_key": "alert-2", "kind": "alert", "summary": "queue depth warning"}
    assert leader.relay(alert_1) == {"ok": False, "status": "queued"}
    assert leader.relay(alert_2) == {"ok": False, "status": "queued"}
    assert leader.relay(alert_1) == {"ok": False, "status": "queued"}
    now = T0 + timedelta(seconds=HEARTBEAT_INTERVAL_S)
    leader.tick()
    assert (
        len(board_posts) == 1
        and "unreachable" in board_posts[0].lower()
        and "security-confidential" not in board_posts[0].lower()
    )
    assert webhook.sent == []

    webhook.down = False
    now = T0 + timedelta(seconds=HEARTBEAT_INTERVAL_S * 2)
    assert leader.tick().heartbeat_sent is True
    assert len(board_posts) == 2 and "recovered" in board_posts[1].lower()
    assert [m.get("dedupe_key") for m in webhook.sent if m.get("kind") == "alert"] == ["alert-1", "alert-2"]
    assert leader.relay(alert_1) == {"ok": True, "status": "deduped"}
    assert [m.get("dedupe_key") for m in webhook.sent if m.get("kind") == "alert"] == ["alert-1", "alert-2"]


@pytest.mark.unit
def test_leader_kill(tmp_path: Path) -> None:
    batch = [_env(f"m_00000000000000000000000{i}") for i in (1, 2, 3, 4)]
    store = BarbContinuityStore(tmp_path / "barb.db")
    acq1 = assume_leadership(store, node="hub-a", ttl_seconds=60, now=T0)
    leader_a = _make_leader(
        tmp_path,
        node="hub-a",
        epoch=acq1["lease"]["epoch"],
        store=store,
        stream=FakeStream(batch, kill_after=2),
        now=T0,
    )
    with pytest.raises(RuntimeError, match="leader kill"):
        leader_a.tick()

    lapsed = T0 + timedelta(seconds=120)
    promoted = assume_leadership(store, node="hub-b", force=True, now=lapsed)
    leader_a._clock = lambda: lapsed
    with pytest.raises(StaleBarbLease):
        leader_a.tick()
    leader_b = _make_leader(
        tmp_path, node="hub-b", epoch=promoted["lease"]["epoch"], store=store, stream=FakeStream(batch), now=lapsed
    )
    report_b = leader_b.tick()
    assert [m["id"] for m in report_b.delivered] == ["m_000000000000000000000003", "m_000000000000000000000004"]
    assert set(leader_b.state.delivered) == {m["id"] for m in batch} and leader_b.tick().delivered == ()


@pytest.mark.unit
def test_hop_limit_loop(tmp_path: Path) -> None:
    stream = FakeStream()
    leader = _make_leader(tmp_path, stream=stream)
    delivered_hops: list[int] = []
    current = _env(
        "m_loop_0", **{"from": "claude", "to": "codex", "summary": "auto-reply ping", "body": "@codex ping", "hops": 0}
    )
    for turn in range(1, 10):
        stream.events.append(current)
        report = leader.tick()
        if report.dropped_ids:
            break
        msg = report.delivered[0]
        delivered_hops.append(int(msg["hops"]))
        sender, recipient = str(msg["to"]), str(msg["from"])
        current = _env(
            f"m_loop_{turn}",
            **{
                "from": sender,
                "to": recipient,
                "summary": f"auto-reply {turn}",
                "body": f"@{recipient} ack {turn}",
                "hops": int(msg["hops"]) + 1,
            },
        )
    assert delivered_hops == list(range(MAX_HOPS + 1))
    assert any(dl.envelope_id == f"m_loop_{MAX_HOPS + 1}" for dl in leader.dead_letters())
