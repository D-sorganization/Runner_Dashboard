"""Runner troubleshoot and fleet scale log the caller by ``Principal.id``.

Both routes logged ``principal.user_id``, which ``Principal`` does not have, so
an authorised call raised AttributeError and came back 502 (or 404 lost behind
it). Found by resolving cross-module imports in mypy (#1734).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from routers import runner_diagnostics

OPERATOR = Principal(id="op", type="human", name="Op", roles=["operator"], scopes=["runners.control"])
RUNNERS = {"runners": [{"id": 1, "name": "d-sorg-fleet-runner-1", "status": "online", "busy": False}]}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def _runners(*_a: Any, **_k: Any) -> dict[str, Any]:
        return RUNNERS

    async def _svc(num: int, action: str, timeout: int = 30) -> tuple[int, str, str]:  # noqa: ARG001
        return 0, "ok", ""

    monkeypatch.setattr(runner_diagnostics, "fetch_org_runners", _runners)
    monkeypatch.setattr(runner_diagnostics, "run_runner_svc", _svc)
    app = FastAPI()
    app.include_router(runner_diagnostics.router)
    app.dependency_overrides[require_scope("runners.control")] = lambda: OPERATOR
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.unit
def test_troubleshoot_known_runner_succeeds(client: TestClient) -> None:
    res = client.post("/api/runners/1/troubleshoot")

    assert res.status_code == 200, res.text
    assert res.json()["runner_id"] == 1


@pytest.mark.unit
def test_troubleshoot_unknown_runner_is_404(client: TestClient) -> None:
    res = client.post("/api/runners/99/troubleshoot")

    assert res.status_code == 404, res.text


@pytest.mark.unit
def test_schedule_scale_reports_utilisation(client: TestClient) -> None:
    res = client.post("/api/runners/fleet/schedule-scale")

    assert res.status_code == 200, res.text
    assert res.json()["utilization_percent"] == 0
