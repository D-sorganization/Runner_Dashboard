"""rd-barb slice 5 (#3073): postman leader, SSE/ETag delivery, wake rules, provider routing & heartbeat."""

from __future__ import annotations

import ast
import datetime as _dt_mod
import inspect
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

if not hasattr(_dt_mod, "UTC"):
    _dt_mod.UTC = _dt_mod.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017

from staff.barb_continuity import BarbContinuityStore
from staff.barb_lease_fence import StaleBarbLease
from staff.barb_node_leader import assume_leadership, claim_for
from staff.watch_tick import ETagCache, ProbeResponse

from scripts import rd_barb_postman as rbp
from scripts.rd_barb_postman import HEARTBEAT_INTERVAL_S, PostmanLeader, classify_wake, route_provider_delivery

UTC = _dt_mod.UTC
T0 = datetime(2026, 10, 29, 15, 0, 0, tzinfo=UTC)
ROSTER = frozenset({"claude", "codex", "antigravity", "cursor", "rd-barb", "barb"})


class FakeStream:
    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self.events = list(events or [])

    def drain(self) -> list[dict[str, Any]]:
        out, self.events = list(self.events), []
        return out


class FakeProbe:
    def __init__(self, responses: list[ProbeResponse] | None = None) -> None:
        self.responses = list(responses or [])
        self.seen_etags: list[str | None] = []

    def get(self, path: str, *, etag: str | None) -> ProbeResponse:
        self.seen_etags.append(etag)
        return self.responses.pop(0) if self.responses else ProbeResponse(status_code=304, etag=etag)


class FakeWebhook:
    def __init__(self, *, down: bool = False) -> None:
        self.down = down
        self.sent: list[dict[str, Any]] = []

    def __call__(self, msg: dict[str, Any]) -> dict[str, Any]:
        if self.down:
            return {"ok": False, "status": "not_sent", "error": "send_failed"}
        self.sent.append(msg)
        return {"ok": True, "status": "sent"}


def _env(mid: str = "m_000000000000000000000001", **over: Any) -> dict[str, Any]:
    base = {
        "id": mid,
        "correlation_id": f"c_{mid[2:]}",
        "kind": "update",
        "from": "claude",
        "to": "rd-barb",
        "summary": "status ping",
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
    continuity = kw.get("store") or BarbContinuityStore(tmp_path / "barb.db")
    epoch: int = kw.get("epoch") or assume_leadership(continuity, node=node, now=now)["lease"]["epoch"]
    bp = kw.get("board_posts")
    posts: list[str] = bp if isinstance(bp, list) else []
    return PostmanLeader(
        continuity=continuity,
        claim=claim_for(node, epoch),
        state_path=tmp_path / "postman_leader_state.json",
        stream=kw.get("stream") or FakeStream(),
        board_probe=kw.get("probe") or FakeProbe(),
        etag_cache=ETagCache(),
        webhook=kw.get("webhook") or FakeWebhook(),
        board_fallback=posts.append,
        roster=ROSTER,
        clock=lambda: now,
    )


@pytest.mark.unit
def test_single_leader_and_manual_promotion_fences_stale_epoch(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "barb.db")
    acq1 = assume_leadership(store, node="hub-a", ttl_seconds=60, now=T0)
    leader_a = _make_leader(tmp_path, node="hub-a", epoch=acq1["lease"]["epoch"], store=store, now=T0)
    lapsed = T0 + timedelta(seconds=120)
    assert assume_leadership(store, node="node-a", now=T0)["acquired"] is False
    promoted = assume_leadership(store, node="node-a", force=True, now=lapsed)
    assert promoted["acquired"] is True and promoted["lease"]["epoch"] == 2
    leader_a._clock = lambda: lapsed
    with pytest.raises(StaleBarbLease):
        leader_a.tick()
    assert _make_leader(tmp_path, node="node-a", epoch=2, store=store, now=lapsed).tick().fenced is False


@pytest.mark.unit
def test_stream_and_etag_delivery_with_idempotent_cursor(tmp_path: Path) -> None:
    e1, e2, e3 = (_env(f"m_00000000000000000000000{i}") for i in (1, 2, 3))
    probe = FakeProbe(
        [
            ProbeResponse(status_code=200, etag='"etag-v1"', body={"messages": [e2]}),
            ProbeResponse(status_code=304, etag='"etag-v1"'),
            ProbeResponse(status_code=200, etag='"etag-v2"', body={"messages": [e2, e3]}),
        ]
    )
    leader = _make_leader(tmp_path, stream=FakeStream([e1, e1]), probe=probe)
    r1 = leader.tick()
    assert [m["id"] for m in r1.delivered] == [e1["id"], e2["id"]] and r1.stream_latency_budget_s <= 5.0
    r2 = leader.tick()
    assert r2.board_not_modified is True and r2.delivered == () and probe.seen_etags[1] == '"etag-v1"'
    assert [m["id"] for m in leader.tick().delivered] == [e3["id"]]
    restarted = _make_leader(
        tmp_path,
        store=leader._continuity,
        epoch=leader._claim.epoch,
        stream=FakeStream([e1]),
        probe=FakeProbe([ProbeResponse(status_code=200, etag='"etag-v2"', body={"messages": [e2, e3]})]),
    )
    assert restarted.tick().delivered == ()


@pytest.mark.unit
def test_mention_only_wake_and_hop_limit_drop(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    directed = _env("m_000000000000000000000010", to="claude", body="please review")
    mentioned = _env("m_000000000000000000000011", to="*", body="heads up @claude check CI")
    broadcast = _env("m_000000000000000000000012", to="*", summary="FYI all", body="general broadcast")
    over_hops = _env("m_000000000000000000000013", to="rd-barb", hops=4)
    assert (
        classify_wake(directed, target_agent="claude"),
        classify_wake(mentioned, target_agent="claude"),
        classify_wake(broadcast, target_agent="claude"),
    ) == ("wake", "wake", "next_turn")
    leader = _make_leader(tmp_path, stream=FakeStream([directed, broadcast, over_hops]))
    with caplog.at_level("WARNING"):
        report = leader.tick()
    assert [m["id"] for m in report.delivered] == [directed["id"], broadcast["id"]]
    assert report.woken == (directed["id"],) and report.dropped_ids == (over_hops["id"],)
    assert any("hop" in rec.message.lower() for rec in caplog.records)
    assert any(dl.envelope_id == over_hops["id"] for dl in leader.dead_letters())


@pytest.mark.unit
@pytest.mark.parametrize(
    ("provider", "env_vars", "settings_json", "hooks_json", "expected_mode"),
    [
        ("claude", {"CLAUDE_CODE_SSE_PORT": "18432"}, "{}", "{}", "push"),
        ("claude", {"CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/claude.sock"}, "{}", "{}", "push"),
        ("claude", {}, '{"channels": {"enabled": true}}', "{}", "push"),
        ("codex", {}, "{}", '{"hooks": {"UserPromptSubmit": [{"command": "x"}]}}', "hook"),
        ("codex", {}, "{}", "{}", "poll"),
        ("antigravity", {}, "{}", '{"hooks": {"Stop": [{"command": "x"}]}}', "hook"),
        ("antigravity", {}, "{}", "{}", "poll"),
        ("cursor-agent", {}, "{}", '{"hooks": {"beforeSubmitPrompt": [{"command": "x"}]}}', "hook"),
        ("cursor-agent", {}, "{}", "{}", "poll"),
    ],
)
def test_provider_routing_selects_socket_channel_hook_or_poll(
    tmp_path: Path, provider: str, env_vars: dict[str, str], settings_json: str, hooks_json: str, expected_mode: str
) -> None:
    dirs = [tmp_path / "claude", tmp_path / "codex", tmp_path / "agy" / ".agents", tmp_path / "cursor"]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    (dirs[0] / "settings.json").write_text(settings_json, encoding="utf-8")
    for d in dirs[1:]:
        (d / "hooks.json").write_text(hooks_json, encoding="utf-8")
    pushed: list[Any] = []
    hooked: list[Any] = []
    polled: list[Any] = []
    route = route_provider_delivery(
        provider,
        _env("m_000000000000000000000050"),
        env={**env_vars, "ANTIGRAVITY_HOME": str(tmp_path / "agy")},
        claude_config_dir=dirs[0],
        codex_home=dirs[1],
        cursor_dir=dirs[3],
        push_sender=pushed.append,
        hook_injector=hooked.append,
        poll_spooler=polled.append,
    )
    counts = {"push": len(pushed), "hook": len(hooked), "poll": len(polled)}
    assert route.mode == expected_mode and counts[expected_mode] == 1 and sum(counts.values()) == 1


@pytest.mark.unit
def test_heartbeat_fires_every_10_min_with_once_per_outage_board_fallback_and_recovery(tmp_path: Path) -> None:
    webhook, now = FakeWebhook(down=False), T0
    board_posts: list[str] = []
    leader = _make_leader(tmp_path, webhook=webhook, board_posts=board_posts, now=now)
    leader._clock = lambda: now
    leader.tick()
    assert len(webhook.sent) == 1 and board_posts == []
    now = T0 + timedelta(minutes=5)
    leader.tick()
    assert len(webhook.sent) == 1
    webhook.down = True
    now = T0 + timedelta(seconds=HEARTBEAT_INTERVAL_S)
    leader.tick()
    assert len(board_posts) == 1 and ("outage" in board_posts[0].lower() or "unreachable" in board_posts[0].lower())
    now = T0 + timedelta(seconds=HEARTBEAT_INTERVAL_S * 2)
    leader.tick()
    assert len(board_posts) == 1
    webhook.down = False
    now = T0 + timedelta(seconds=HEARTBEAT_INTERVAL_S * 3)
    leader.tick()
    assert len(webhook.sent) == 2 and len(board_posts) == 2 and "recover" in board_posts[1].lower()


@pytest.mark.unit
def test_dry_run_plans_and_writes_nothing_and_no_dispatch_imports(tmp_path: Path) -> None:
    webhook = FakeWebhook()
    board_posts: list[str] = []
    leader = _make_leader(
        tmp_path, stream=FakeStream([_env("m_000000000000000000000077")]), webhook=webhook, board_posts=board_posts
    )
    report = leader.tick(dry_run=True)
    assert report.dry_run is True and len(report.planned_ids) == 1 and report.delivered == ()
    assert webhook.sent == [] and board_posts == [] and not (tmp_path / "postman_leader_state.json").exists()
    tree = ast.parse(inspect.getsource(rbp))
    modules = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names] + [
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    ]
    banned = ("dispatch", "staff_run", "cloud_run", "work_request")
    assert [m for m in modules if any(b in m.lower() for b in banned)] == []
