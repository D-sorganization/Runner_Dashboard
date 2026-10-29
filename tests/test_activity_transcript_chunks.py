"""Bounded transcript chunk store and chunk API tests (#2866)."""

from __future__ import annotations

import gzip
from pathlib import Path

import activity_transcript as at
import pytest
from activity import ActivityEnvelope, ActivityStore
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal
from routers import activity


def _meta(seq: int = 1, session_id: str = "session-a", **changes: object) -> ActivityEnvelope:
    base: dict[str, object] = {
        "schema_version": 1,
        "event_id": f"meta-{session_id}-{seq}",
        "source": "recorder",
        "principal": "agent-a",
        "node": "node-a",
        "session_id": session_id,
        "seq": seq,
        "ts": "2026-10-29T12:00:00Z",
        "kind": "output",
        "redaction_version": 1,
        "payload": {"bytes": 12},
    }
    base.update(changes)
    return ActivityEnvelope.model_validate(base)


@pytest.fixture
def env_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    at.reset_activity_metrics()
    act_store = ActivityStore(tmp_path / "activity.sqlite3")
    chunk_store = at.TranscriptChunkStore(tmp_path / "spool", tmp_path / "chunks.sqlite3")
    monkeypatch.setattr(activity, "get_activity_store", lambda: act_store)
    monkeypatch.setattr(activity, "get_transcript_chunk_store", lambda: chunk_store)
    monkeypatch.setattr(at, "get_transcript_chunk_store", lambda: chunk_store)
    return act_store, chunk_store


def _client(monkeypatch: pytest.MonkeyPatch, principal: Principal) -> TestClient:
    import identity

    app = FastAPI()
    app.include_router(activity.router)
    monkeypatch.setattr(identity, "_resolve_principal_optional", lambda *_args: principal)
    return TestClient(app)


def test_chunk_post_get_after_seq_ansi_and_idempotency(env_stores, monkeypatch: pytest.MonkeyPatch) -> None:
    act_store, chunk_store = env_stores
    act_store.ingest([_meta(1), _meta(2)], principal="agent-a")
    writer = Principal(id="agent-a", type="bot", name="fixture", scopes=["activity.write", "activity.read"])
    url = "/api/v1/activity/sessions/session-a/chunks"
    with _client(monkeypatch, writer) as client:
        first = client.post(
            url, json={"seq": 1, "node": "node-a", "data": "\x1b[32mgreen\x1b[0m line\n", "redaction_version": 99}
        )
        assert first.status_code == 200
        body1 = first.json()
        assert (body1["seq"], body1["raw"], body1["text"], body1["redaction_version"]) == (
            1,
            "\x1b[32mgreen\x1b[0m line\n",
            "green line\n",
            1,
        )
        retry = client.post(url, json={"seq": 1, "node": "node-a", "data": "\x1b[32mgreen\x1b[0m line\n"})
        assert retry.status_code == 200 and retry.json()["byte_len"] == body1["byte_len"]

        conflict = client.post(url, json={"seq": 1, "node": "node-a", "data": "different line\n"})
        assert conflict.status_code == 409 and at.activity_metrics_snapshot(chunk_store)["reject_409"] == 1

        assert client.post(url, json={"seq": 2, "node": "node-a", "data": "second chunk\n"}).status_code == 200
        page_all = client.get(url, params={"after_seq": 0}).json()
        assert [c["seq"] for c in page_all["items"]] == [1, 2] and page_all["gaps"] == []
        page_after_1 = client.get(url, params={"after_seq": 1}).json()
        assert [c["seq"] for c in page_after_1["items"]] == [2]


def test_redaction_before_persistence_and_gzip_at_rest(env_stores, monkeypatch: pytest.MonkeyPatch) -> None:
    act_store, chunk_store = env_stores
    act_store.ingest([_meta(1)], principal="agent-a")
    secret_token = "ghp_" + "a" * 36
    secret_ip = ".".join(["10", "42", "1", "9"])
    writer = Principal(id="agent-a", type="bot", name="fixture", scopes=["activity.write", "activity.read"])
    with _client(monkeypatch, writer) as client:
        res = client.post(
            "/api/v1/activity/sessions/session-a/chunks",
            json={"seq": 1, "node": "node-a", "data": f"\x1b[31m{secret_token}\x1b[0m from {secret_ip}"},
        )
        assert res.status_code == 200
        payload = res.json()
        assert secret_token not in payload["raw"] and secret_ip not in payload["raw"]
        assert "REDACTED" in payload["raw"] and "\x1b[31m" in payload["raw"] and "\x1b[31m" not in payload["text"]

    gz_files = list(chunk_store.spool_dir.rglob("*.gz"))
    assert len(gz_files) == 1
    with gzip.open(gz_files[0], "rt", encoding="utf-8") as handle:
        on_disk = handle.read()
    assert secret_token not in on_disk and secret_ip not in on_disk and "REDACTED" in on_disk


def test_capture_gap_recorded_when_metadata_or_chunk_missing(env_stores, monkeypatch: pytest.MonkeyPatch) -> None:
    act_store, _ = env_stores
    writer = Principal(id="agent-a", type="bot", name="fixture", scopes=["activity.write", "activity.read"])
    url = "/api/v1/activity/sessions/session-a/chunks"
    with _client(monkeypatch, writer) as client:
        assert client.post(url, json={"seq": 1, "node": "node-a", "data": "orphan chunk\n"}).status_code == 200
        gaps_after_orphan = [e for e in act_store.read(session_id="session-a").items if e.kind == "CaptureGap"]
        assert any(
            g.payload.get("chunk_seq") == 1 and g.payload.get("reason") == "missing_metadata" for g in gaps_after_orphan
        )
        act_store.ingest([_meta(2)], principal="agent-a")
        page = client.get(url, params={"after_seq": 0})
        assert page.status_code == 200
        gap_seqs = {(g["payload"]["chunk_seq"], g["payload"]["reason"]) for g in page.json()["gaps"]}
        assert (1, "missing_metadata") in gap_seqs and (2, "missing_chunk") in gap_seqs


def test_per_chunk_413_and_session_node_429_caps(env_stores, monkeypatch: pytest.MonkeyPatch) -> None:
    act_store, chunk_store = env_stores
    act_store.ingest([_meta(1), _meta(2), _meta(3)], principal="agent-a")
    writer = Principal(id="agent-a", type="bot", name="fixture", scopes=["activity.write", "activity.read"])
    monkeypatch.setenv("RUNNER_DASHBOARD_ACTIVITY_MAX_CHUNK_BYTES", "32")
    monkeypatch.setenv("RUNNER_DASHBOARD_ACTIVITY_SESSION_MAX_BYTES", "20")
    url = "/api/v1/activity/sessions/session-a/chunks"
    with _client(monkeypatch, writer) as client:
        assert client.post(url, json={"seq": 1, "node": "node-a", "data": "x" * 40}).status_code == 413
        assert at.activity_metrics_snapshot(chunk_store)["reject_413"] == 1
        assert client.post(url, json={"seq": 2, "node": "node-a", "data": "012345678901234"}).status_code == 200
        s_cap = client.post(url, json={"seq": 3, "node": "node-a", "data": "0123456789"})
        assert s_cap.status_code == 429 and int(s_cap.headers["Retry-After"]) > 0
        assert at.activity_metrics_snapshot(chunk_store)["reject_429"] == 1

    monkeypatch.setenv("RUNNER_DASHBOARD_ACTIVITY_SESSION_MAX_BYTES", "1000")
    monkeypatch.setenv("RUNNER_DASHBOARD_ACTIVITY_NODE_MAX_BYTES", "20")
    with _client(monkeypatch, writer) as client:
        n_cap = client.post(
            "/api/v1/activity/sessions/session-b/chunks", json={"seq": 1, "node": "node-a", "data": "0123456789"}
        )
        assert n_cap.status_code == 429


def test_scope_and_session_principal_binding(env_stores, monkeypatch: pytest.MonkeyPatch) -> None:
    act_store, _ = env_stores
    act_store.ingest([_meta(1, principal="agent-a")], principal="agent-a")
    url = "/api/v1/activity/sessions/session-a/chunks"
    other = Principal(id="agent-b", type="bot", name="fixture", scopes=["activity.write", "activity.read"])
    with _client(monkeypatch, other) as client:
        assert client.post(url, json={"seq": 1, "node": "node-a", "data": "hijack"}).status_code == 403
    read_only = Principal(id="agent-a", type="bot", name="fixture", scopes=["activity.read"])
    with _client(monkeypatch, read_only) as client:
        assert client.post(url, json={"seq": 1, "node": "node-a", "data": "hello"}).status_code == 403
