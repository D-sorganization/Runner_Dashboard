"""Admin API for dispatch overlap sessions."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from dispatch_queue_link import reset_dispatch_queue_link_state
from dispatch_routing import dispatch_queue_manager
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope


@pytest.fixture(autouse=True)
def _reset_queue() -> None:
    reset_dispatch_queue_link_state()
    yield
    reset_dispatch_queue_link_state()


def _client(admin: Principal) -> Iterator[TestClient]:
    from routers import staff_dispatch_sessions as mod  # noqa: PLC0415

    app = FastAPI()
    app.include_router(mod.router, prefix="/api/v1/staff")
    app.dependency_overrides[require_scope("staff.admin")] = lambda: admin
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.unit
def test_list_and_release_dispatch_sessions() -> None:
    admin = Principal(
        id="op",
        type="human",
        name="Op",
        roles=["operator"],
        scopes=["staff.admin", "staff.read"],
    )
    attempt = dispatch_queue_manager.try_dispatch(
        repository="Runner_Dashboard",
        number=1,
        prompt="`backend/z.py`",
        occupies_paths=True,
        role="ad-hoc",
    )
    assert attempt.session is not None
    sid = attempt.session.session_id

    client = next(_client(admin))
    list_resp = client.get("/api/v1/staff/dispatch-sessions")
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert body["count"] == 1
    row = body["sessions"][0]
    assert row["session_id"] == sid
    assert row["repo"] == "Runner_Dashboard"
    assert row["role"] == "ad-hoc"
    assert "backend/z.py" in row["declared_paths"]

    del_resp = client.delete(f"/api/v1/staff/dispatch-sessions/{sid}")
    assert del_resp.status_code == 200
    assert del_resp.json()["released"] is True
    assert dispatch_queue_manager.get_active_sessions() == []


@pytest.mark.unit
def test_dispatch_sessions_require_admin() -> None:
    viewer = Principal(id="v", type="human", name="V", roles=["viewer"], scopes=["staff.read"])
    app = FastAPI()
    from routers import staff_dispatch_sessions as mod  # noqa: PLC0415

    app.include_router(mod.router, prefix="/api/v1/staff")
    # No override: require_scope should reject viewer via normal path — use minimal app without override
    client = TestClient(app)
    resp = client.get("/api/v1/staff/dispatch-sessions")
    assert resp.status_code in (401, 403)