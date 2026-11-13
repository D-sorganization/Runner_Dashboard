"""GET /api/v1/activity/sessions (#2860): scope-gated, bounded, restart-stable."""

from __future__ import annotations

from datetime import UTC, datetime

from activity import ActivityEnvelope, ActivityStore
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal
from routers import activity

NOW = datetime(2026, 10, 28, 12, 10, 0, tzinfo=UTC)


def envelope(session_id="session-a", seq=1, **overrides):
    values = dict(
        schema_version=1,
        event_id=f"{overrides.get('source', 'staff_run')}:{session_id}:{seq}",
        source="staff_run",
        principal="agent-a",
        node="node-a",
        session_id=session_id,
        seq=seq,
        ts="2026-10-28T12:09:00Z",
        kind="heartbeat",
        redaction_version=1,
        payload={},
    )
    values.update(overrides)
    values["event_id"] = f"{values['source']}:{session_id}:{seq}"
    return ActivityEnvelope.model_validate(values)


def make_app(store, monkeypatch, principal):
    app = FastAPI()
    app.include_router(activity.router)
    route = next(route for route in activity.router.routes if route.path.endswith("/sessions"))
    dependency = route.dependant.dependencies[0].call
    app.dependency_overrides[dependency] = lambda: principal
    monkeypatch.setattr(activity, "get_activity_store", lambda: store)
    return app


def test_requires_activity_read_scope(tmp_path, monkeypatch):
    store = ActivityStore(tmp_path / "activity.sqlite")
    store.ingest([envelope()], principal="agent-a")
    app = FastAPI()
    app.include_router(activity.router)
    monkeypatch.setattr(activity, "get_activity_store", lambda: store)
    import identity

    no_scope = Principal(id="agent-a", type="bot", name="fixture", scopes=[])
    monkeypatch.setattr(identity, "_resolve_principal_optional", lambda *_args: no_scope)
    with TestClient(app) as client:
        response = client.get("/api/v1/activity/sessions")
        assert response.status_code == 403


def test_returns_bounded_sessions_list(tmp_path, monkeypatch):
    store = ActivityStore(tmp_path / "activity.sqlite")
    store.ingest([envelope()], principal="agent-a")
    reader = Principal(id="reader", type="bot", name="fixture", scopes=["activity.read"])
    app = make_app(store, monkeypatch, reader)
    with TestClient(app) as client:
        response = client.get("/api/v1/activity/sessions")
        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["session_id"] == "session-a"
        assert body["items"][0]["status"] in {"live", "idle", "stale", "ended", "uncaptured"}


def test_filters_by_query_params(tmp_path, monkeypatch):
    store = ActivityStore(tmp_path / "activity.sqlite")
    store.ingest([envelope(session_id="a", node="node-a", repo="r/a")], principal="agent-a")
    store.ingest(
        [envelope(session_id="b", node="node-b", repo="r/b", principal="agent-b")],
        principal="agent-b",
    )
    reader = Principal(id="reader", type="bot", name="fixture", scopes=["activity.read"])
    app = make_app(store, monkeypatch, reader)
    with TestClient(app) as client:
        response = client.get("/api/v1/activity/sessions", params={"node": "node-a"})
        assert [item["session_id"] for item in response.json()["items"]] == ["a"]
        response = client.get("/api/v1/activity/sessions", params={"repo": "r/b"})
        assert [item["session_id"] for item in response.json()["items"]] == ["b"]


def test_malformed_status_query_param_fails_closed(tmp_path, monkeypatch):
    store = ActivityStore(tmp_path / "activity.sqlite")
    reader = Principal(id="reader", type="bot", name="fixture", scopes=["activity.read"])
    app = make_app(store, monkeypatch, reader)
    with TestClient(app) as client:
        response = client.get("/api/v1/activity/sessions", params={"status": "deleted"})
        assert response.status_code == 422


def test_empty_store_reports_complete_empty_over_the_wire(tmp_path, monkeypatch):
    """#3503: a client reading an empty page must be able to tell that the hub
    never recorded anything, rather than assuming no agents exist."""
    store = ActivityStore(tmp_path / "activity.sqlite")
    reader = Principal(id="reader", type="bot", name="fixture", scopes=["activity.read"])
    app = make_app(store, monkeypatch, reader)
    with TestClient(app) as client:
        response = client.get("/api/v1/activity/sessions")
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["empty_reason"] == "complete_empty"


def test_filtered_empty_reports_local_empty_over_the_wire(tmp_path, monkeypatch):
    store = ActivityStore(tmp_path / "activity.sqlite")
    store.ingest([envelope(node="node-a")], principal="agent-a")
    reader = Principal(id="reader", type="bot", name="fixture", scopes=["activity.read"])
    app = make_app(store, monkeypatch, reader)
    with TestClient(app) as client:
        response = client.get("/api/v1/activity/sessions", params={"node": "node-missing"})
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["empty_reason"] == "local_empty"


def test_unauthorized_response_never_echoes_the_caller_supplied_token(tmp_path, monkeypatch):
    """#3503: unauthorized must be distinguishable (403) without leaking credentials."""
    store = ActivityStore(tmp_path / "activity.sqlite")
    app = FastAPI()
    app.include_router(activity.router)
    monkeypatch.setattr(activity, "get_activity_store", lambda: store)
    import identity

    no_scope = Principal(id="agent-a", type="bot", name="fixture", scopes=[])
    monkeypatch.setattr(identity, "_resolve_principal_optional", lambda *_args: no_scope)
    caller_supplied_bearer_value = "fixture-bearer-must-not-appear-in-body"
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/activity/sessions", headers={"Authorization": f"Bearer {caller_supplied_bearer_value}"}
        )
        assert response.status_code == 403
        assert caller_supplied_bearer_value not in response.text


def test_session_summary_never_implies_transcript_capture():
    """#3503: registration/activity metadata must not claim transcript capture
    that was never performed -- no field name in the response contract may
    imply raw transcript/terminal content capture."""
    from activity_sessions import SessionSummary

    field_names = set(SessionSummary.model_fields)
    assert not any("transcript" in name or "capture" in name for name in field_names)


def test_limit_is_bounded(tmp_path, monkeypatch):
    store = ActivityStore(tmp_path / "activity.sqlite")
    reader = Principal(id="reader", type="bot", name="fixture", scopes=["activity.read"])
    app = make_app(store, monkeypatch, reader)
    with TestClient(app) as client:
        response = client.get("/api/v1/activity/sessions", params={"limit": 0})
        assert response.status_code == 422
        response = client.get("/api/v1/activity/sessions", params={"limit": 501})
        assert response.status_code == 422
