"""HTTP layer of the CR-4 planner stage (#1285): auth, payload contracts, error mapping.

The planning flow itself is covered end to end in tests/code_requests/test_planner_stage.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from code_requests.plan_service import PlanningError  # noqa: E402
from code_requests.plan_store import PlanSessionStore  # noqa: E402
from code_requests.planner import PlanningSession  # noqa: E402
from routers import code_request_plans  # noqa: E402
from server import app  # noqa: E402

HEADERS = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PlanSessionStore:
    store = PlanSessionStore(tmp_path / "plans.json")
    monkeypatch.setattr(code_request_plans, "_sessions", store)
    return store


def test_planner_routes_require_authentication(sessions: PlanSessionStore) -> None:
    client = TestClient(app, headers=HEADERS)
    for method, path in [
        ("get", "/api/code-requests/cr-1/plan"),
        ("post", "/api/code-requests/cr-1/plan/start"),
        ("post", "/api/code-requests/cr-1/plan/approve"),
    ]:
        assert getattr(client, method)(path).status_code in (401, 403), path


def test_get_plan_returns_the_session_or_404(mock_auth: Any, sessions: PlanSessionStore) -> None:
    client = TestClient(app, headers=HEADERS)
    assert client.get("/api/code-requests/cr-1/plan").status_code == 404
    sessions.save(PlanningSession(request_id="cr-1", attempts=1))
    body = client.get("/api/code-requests/cr-1/plan").json()
    assert body["status"] == "awaiting_plan" and body["attempts"] == 1


def test_an_empty_submission_is_refused_by_the_payload_contract(mock_auth: Any, sessions: PlanSessionStore) -> None:
    client = TestClient(app, headers=HEADERS)
    assert client.post("/api/code-requests/cr-1/plan", json={"output": ""}).status_code == 422


@pytest.mark.parametrize("status", [404, 409, 422, 502])
def test_planning_errors_map_to_their_http_status(
    mock_auth: Any, sessions: PlanSessionStore, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    async def refuse(*_: Any, **__: Any) -> Any:
        raise PlanningError(status, "explained refusal")

    monkeypatch.setattr(code_request_plans, "approve_plan", refuse)
    monkeypatch.setattr(code_request_plans, "_deps", lambda: None)
    res = TestClient(app, headers=HEADERS).post("/api/code-requests/cr-1/plan/approve")
    assert res.status_code == status
    assert res.json()["detail"] == "explained refusal"
