"""Authorized transcript SSE tail, replay, bounded subscriber buffering, and peer federation tests (#2867)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import activity_transcript as at
import httpx
import pytest
from activity import ActivityEnvelope, ActivityStore
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal
from routers import activity
from staff import remote_runs


def _meta(seq: int = 1, session_id: str = "session-a") -> ActivityEnvelope:
    base: dict[str, object] = {"schema_version": 1, "event_id": f"meta-{session_id}-{seq}", "source": "recorder"}
    base.update(principal="agent-a", node="node-a", session_id=session_id, seq=seq, ts="2026-10-29T12:00:00Z")
    base.update(kind="output", redaction_version=1, payload={"bytes": 12})
    return ActivityEnvelope.model_validate(base)


@pytest.fixture
def env_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    at.reset_activity_metrics()
    remote_runs._SESSION_PEER_CACHE.clear()
    act_store = ActivityStore(tmp_path / "activity.sqlite3")
    chunk_store = at.TranscriptChunkStore(tmp_path / "spool", tmp_path / "chunks.sqlite3")
    monkeypatch.setattr(activity, "get_activity_store", lambda: act_store)
    monkeypatch.setattr(activity, "get_transcript_chunk_store", lambda: chunk_store)
    monkeypatch.setattr(at, "get_transcript_chunk_store", lambda: chunk_store)
    return act_store, chunk_store


def _client(monkeypatch: pytest.MonkeyPatch, principal: Principal | None) -> TestClient:
    import identity

    app = FastAPI()
    app.include_router(activity.router)
    monkeypatch.setattr(identity, "_resolve_principal_optional", lambda *_args: principal)
    return TestClient(app)


def _parse_sse(raw_text: str) -> list[tuple[str | None, str, dict[str, Any]]]:
    events: list[tuple[str | None, str, dict[str, Any]]] = []
    for block in raw_text.strip().split("\n\n"):
        if not block.strip() or block.strip().startswith(":"):
            continue
        ev_id, ev_type, data_str = None, "message", ""
        for line in block.splitlines():
            if line.startswith("id: "):
                ev_id = line[4:].strip()
            elif line.startswith("event: "):
                ev_type = line[7:].strip()
            elif line.startswith("data: "):
                data_str = line[6:].strip()
        if data_str:
            events.append((ev_id, ev_type, json.loads(data_str)))
    return events


def test_sse_replay_from_after_seq_and_last_event_id_without_gaps_or_duplicates(
    env_stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    act_store, _ = env_stores
    act_store.ingest([_meta(1), _meta(2), _meta(3)], principal="agent-a")
    user = Principal(id="agent-a", type="bot", name="fixture", scopes=["activity.write", "activity.read"])
    chunks_url, stream_url = "/api/v1/activity/sessions/session-a/chunks", "/api/v1/activity/sessions/session-a/stream"
    with _client(monkeypatch, user) as client:
        for seq, text in ((1, "first\n"), (2, "second\n"), (3, "third\n")):
            assert client.post(chunks_url, json={"seq": seq, "node": "node-a", "data": text}).status_code == 200
        full = client.get(stream_url, params={"after_seq": 0, "follow": "false"})
        assert [(eid, etype, d["seq"]) for eid, etype, d in _parse_sse(full.text)] == [
            (str(i), "chunk", i) for i in (1, 2, 3)
        ]
        resumed_q = client.get(stream_url, params={"after_seq": 1, "follow": "false"})
        assert [d["seq"] for _, _, d in _parse_sse(resumed_q.text)] == [2, 3]
        resumed_h = client.get(stream_url, params={"follow": "false"}, headers={"Last-Event-ID": "2"})
        assert [d["seq"] for _, _, d in _parse_sse(resumed_h.text)] == [3]


def test_live_sse_tail_dedupes_replay_boundary_and_disconnects_slow_consumer(
    env_stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    act_store, _ = env_stores
    act_store.ingest([_meta(1), _meta(2), _meta(3), _meta(4)], principal="agent-a")
    monkeypatch.setenv("RUNNER_DASHBOARD_ACTIVITY_SSE_BUFFER_SIZE", "2")
    user = Principal(id="agent-a", type="bot", name="fixture", scopes=["activity.write", "activity.read"])
    chunks_url, stream_url = "/api/v1/activity/sessions/session-a/chunks", "/api/v1/activity/sessions/session-a/stream"
    with _client(monkeypatch, user) as client:
        assert client.post(chunks_url, json={"seq": 1, "node": "node-a", "data": "c1\n"}).status_code == 200
        orig_subscribe = activity.subscribe_transcript_session

        async def _preloaded_subscribe(session_id: str, max_buffer: int | None = None):
            q = await orig_subscribe(session_id, max_buffer=2)
            for seq in (1, 2, 3, 4):
                payload = {"session_id": session_id, "seq": seq, "text": f"c{seq}\n"}
                activity.publish_transcript_event(session_id, "chunk", payload, seq=seq)
            return q

        monkeypatch.setattr(activity, "subscribe_transcript_session", _preloaded_subscribe)
        resp = client.get(stream_url, params={"after_seq": 0, "follow": "true"})
        stream_events = _parse_sse(resp.text)
        assert [d["seq"] for _, etype, d in stream_events if etype == "chunk"].count(1) == 1
        disc = {"session_id": "session-a", "reason": "buffer_overflow", "policy": "disconnect_slow_consumer"}
        assert stream_events[-1][1:] == ("slow_consumer_disconnect", disc)


def test_scope_activity_read_required_and_peer_federation_never_recurses(
    env_stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_read = Principal(id="agent-checkin", type="bot", name="fixture", scopes=["activity.checkin"])
    with _client(monkeypatch, no_read) as client:
        assert client.get("/api/v1/activity/sessions/session-a/stream").status_code == 403
        assert client.get("/api/v1/activity/local-sessions/session-a/chunks").status_code == 403
        assert client.get("/api/v1/activity/local-sessions/session-a/stream").status_code == 403

    reader = Principal(id="operator-1", type="human", name="Operator", scopes=["activity.read"])
    requests: list[str] = []

    async def fake_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        requests.append(url)
        if "peer-a:8321/api/v1/activity/local-sessions/remote-sess" in url:
            item: dict[str, Any] = {"session_id": "remote-sess", "seq": 1, "node": "peer-a", "principal": "agent-a"}
            item.update(raw="remote line\n", text="remote line\n", data="remote line\n", byte_len=12, stored_at=1.0)
            return {"session_id": "remote-sess", "items": [item], "gaps": [], "next_seq": None}
        req = httpx.Request("GET", url)
        detail = {"detail": {"code": "local_session_not_found", "session_id": "missing-sess"}}
        raise httpx.HTTPStatusError("not found", request=req, response=httpx.Response(404, request=req, json=detail))

    class FakeStream:
        status_code = 200

        async def aiter_lines(self) -> AsyncIterator[str]:
            for line in ("id: 1", "event: chunk", 'data: {"session_id":"remote-sess","seq":1}', ""):
                yield line

        async def __aenter__(self) -> FakeStream:
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

    class FakeClient:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        def stream(self, _method: str, url: str, **__: Any) -> FakeStream:
            requests.append(url)
            return FakeStream()

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

    monkeypatch.setattr("staff.fleet.get_json", fake_get_json)
    monkeypatch.setattr("staff.fleet.peer_nodes", lambda: {"peer-a": "http://peer-a:8321"})
    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    with _client(monkeypatch, reader) as client:
        chunks_resp = client.get("/api/v1/activity/sessions/remote-sess/chunks", params={"after_seq": 0})
        assert chunks_resp.status_code == 200 and chunks_resp.json()["items"][0]["text"] == "remote line\n"
        assert all("/local-sessions/remote-sess" in u for u in requests)

        requests.clear()
        stream_resp = client.get("/api/v1/activity/sessions/remote-sess/stream", params={"after_seq": 0})
        assert stream_resp.status_code == 200 and _parse_sse(stream_resp.text)[0][2]["seq"] == 1
        assert any("/api/v1/activity/local-sessions/remote-sess/stream?after_seq=0" in u for u in requests)
        assert all("/api/v1/activity/sessions/" not in u for u in requests)

        monkeypatch.setattr("staff.fleet.peer_nodes", lambda: pytest.fail("local-sessions must not fan out"))
        local_miss = client.get("/api/v1/activity/local-sessions/missing-sess/chunks")
        assert local_miss.status_code == 404
        assert local_miss.json()["detail"] == {"code": "local_session_not_found", "session_id": "missing-sess"}
        assert client.get("/api/v1/activity/local-sessions/missing-sess/stream").status_code == 404

    fleet_peer = Principal(id="fleet-peer", type="bot", name="Fleet Peer", roles=["fleet-peer"])
    with _client(monkeypatch, fleet_peer) as peer_client:
        assert peer_client.get("/api/v1/activity/sessions/missing-sess/stream").status_code == 404
        assert peer_client.get("/api/v1/activity/sessions/missing-sess/chunks").json()["items"] == []
