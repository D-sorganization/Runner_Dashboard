"""Atomic ingestion contract; all state is temporary and requests are local."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from activity import ActivityEnvelope, ActivityStore
from fastapi import HTTPException


def event(seq=1, **changes):
    data = dict(
        schema_version=1,
        event_id=f"event-{seq}",
        source="recorder",
        principal="agent-a",
        node="node-a",
        session_id="session-a",
        seq=seq,
        ts="2026-10-27T12:00:00Z",
        kind="output",
        redaction_version=999,
        payload={"text": "safe"},
    )
    data.update(changes)
    return ActivityEnvelope.model_validate(data)


@pytest.fixture
def store(tmp_path):
    return ActivityStore(tmp_path / "activity.sqlite")


def ingest(store, events, principal="agent-a", **kwargs):
    return store.ingest(events, principal=principal, **kwargs)


def test_commit_is_durable_and_lost_ack_retry_is_free(store):
    first = ingest(store, [event(), event(2)], max_events=2)
    reopened = ActivityStore(store.db_path)
    assert ingest(reopened, [event(), event(2)], max_events=2) == first
    assert len(reopened.read().items) == 2
    with pytest.raises(HTTPException) as error:
        ingest(reopened, [event(3)], max_events=2)
    assert error.value.status_code == 429


def test_conflict_rolls_back_entire_batch(store):
    ingest(store, [event()])
    with pytest.raises(HTTPException) as error:
        ingest(store, [event(2), event(payload={"text": "changed"})])
    assert error.value.status_code == 409
    assert len(store.read().items) == 1


def test_retry_ignores_json_key_order_and_source_redaction_claim(store):
    first = ingest(store, [event(payload={"b": 2, "a": 1})])
    assert ingest(store, [event(payload={"a": 1, "b": 2}, redaction_version=5)]) == first


@pytest.mark.parametrize(
    "changes",
    [
        {"principal": "agent-b"},
        {"principal": "agent-b", "node": "node-b", "source": "desktop_adapter"},
        {"principal": "agent-b", "session_id": "session-b"},
    ],
)
def test_another_principal_cannot_take_session_or_source(store, changes):
    ingest(store, [event()])
    with pytest.raises(HTTPException) as error:
        ingest(store, [event(2, **changes)], principal="agent-b")
    assert error.value.status_code == 403
    assert len(store.read().items) == 1


def test_spoofed_principal_is_denied(store):
    with pytest.raises(HTTPException) as error:
        ingest(store, [event()], principal="agent-b")
    assert error.value.status_code == 403


def test_concurrent_connections_cannot_claim_same_session(store):
    from threading import Barrier

    barrier = Barrier(2)

    def write(principal):
        connection = ActivityStore(store.db_path)
        barrier.wait(timeout=5)
        try:
            ingest(connection, [event(principal=principal)], principal=principal)
            return 200
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(write, ["agent-a", "agent-b"])) == [200, 403]
    assert len(store.read().items) == 1


def test_legacy_append_records_reserve_their_session(store):
    store.append(event())
    with pytest.raises(HTTPException) as error:
        ingest(store, [event(2, principal="agent-b", node="node-b")], principal="agent-b")
    assert error.value.status_code == 403


def test_conflict_does_not_charge_quota(store):
    ingest(store, [event()], max_events=2)
    with pytest.raises(HTTPException):
        ingest(store, [event(2), event(payload={"text": "conflict"})], max_events=2)
    ingest(store, [event(3)], max_events=2)
    assert [item.seq for item in store.read().items] == [1, 3]


def test_independent_principals_have_independent_quotas_and_sources(store):
    ingest(store, [event()], max_events=1)
    ingest(
        store, [event(2, principal="agent-b", node="node-b", session_id="session-b")], principal="agent-b", max_events=1
    )
    assert len(store.read().items) == 2


@pytest.mark.parametrize("limits", [{"max_events": 1}, {"max_bytes": 1}])
def test_quota_rolls_back_batch_and_returns_retry_after(store, limits):
    with pytest.raises(HTTPException) as error:
        ingest(store, [event(), event(2)], **limits)
    assert error.value.status_code == 429
    assert int(error.value.headers["Retry-After"]) > 0
    assert store.read().items == []


def test_quota_window_expires(store):
    ingest(store, [event()], max_events=1, now=10)
    with pytest.raises(HTTPException):
        ingest(store, [event(2)], max_events=1, now=11)
    ingest(store, [event(2)], max_events=1, now=71)
    assert len(store.read().items) == 2


def test_redaction_happens_before_storage_including_keys_and_metadata(store):
    secrets = [
        "ghp_" + "x" * 36,
        "sk-" + "x" * 24,
        "Bearer " + "x" * 24,
        "\n".join(["-----BEGIN " + "PRIVATE KEY-----", "fixture", "-----END " + "PRIVATE KEY-----"]),
        ".".join(map(str, [10, 23, 45, 67])),
    ]
    raw = event(branch=secrets[0], payload={value: [value] for value in secrets})
    saved = ingest(store, [raw])[0]
    assert saved.redaction_version == 1
    conn = sqlite3.connect(store.db_path)
    encoded = conn.execute("SELECT envelope FROM activity_events").fetchone()[0]
    conn.close()
    for secret in secrets:
        assert secret not in encoded
    assert "REDACTED" in encoded


def test_http_bounds_scope_and_ingestion(store, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from identity import Principal
    from routers import activity

    app = FastAPI()
    app.include_router(activity.router)
    dependency = (
        next(route for route in activity.router.routes if route.path.endswith("/events")).dependant.dependencies[0].call
    )
    app.dependency_overrides[dependency] = lambda: Principal(id="agent-a", type="bot", name="fixture")
    monkeypatch.setattr(activity, "get_activity_store", lambda: store)
    with TestClient(app) as client:
        assert client.post("/api/v1/activity/events", json=[event().model_dump(mode="json")]).status_code == 200
        assert client.post("/api/v1/activity/events", content=b" " * 262145).status_code == 413
        assert client.post("/api/v1/activity/events", json=[]).status_code == 422
        assert (
            client.post(
                "/api/v1/activity/events", json=[event(2, principal="other").model_dump(mode="json")]
            ).status_code
            == 403
        )
    # Exercise the actual scope dependency, rather than just its test override.
    import identity

    app.dependency_overrides.clear()
    reader = Principal(id="agent-a", type="bot", name="fixture", scopes=["activity.read"])
    monkeypatch.setattr(identity, "_resolve_principal_optional", lambda *_args: reader)
    with TestClient(app) as client:
        response = client.post("/api/v1/activity/events", json=[event(2).model_dump(mode="json")])
        assert response.status_code == 403
        assert response.json()["detail"]["required_scope"] == "activity.write"
