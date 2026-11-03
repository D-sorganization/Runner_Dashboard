"""GET /api/directives/drain hub contract (#3915 slice 1)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from identity import require_fleet_peer
from priorities.directives import Directive
from server import app


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.mark.unit
def test_directives_drain_requires_repo(client: TestClient) -> None:
    assert client.get("/api/directives/drain").status_code == 422


@pytest.mark.unit
def test_directives_drain_reports_lifted_when_open_prs_below_threshold(client: TestClient) -> None:
    directive = Directive(
        id="dir-gm",
        text="Drain mode on Gasification_Model until 2026-11-05 07:59Z",
        set_by="operator",
        repo="Gasification_Model",
        expires="2026-11-05T07:59:00Z",
    )
    now = datetime(2026, 11, 3, 14, 0, tzinfo=UTC)
    with (
        patch("routers.directives_drain.get_directives") as mock_list,
        patch(
            "routers.directives_drain.fetch_open_non_draft_pr_count",
            new=AsyncMock(return_value=(12, None)),
        ),
        patch("routers.directives_drain.datetime") as mock_dt,
    ):
        mock_list.return_value.active.return_value = [directive]
        mock_dt.now.return_value = now
        res = client.get("/api/directives/drain", params={"repo": "D-sorganization/Gasification_Model"})
    assert res.status_code == 200
    body = res.json()
    assert body["drained"] is False
    assert body["open_pr_count"] == 12
    assert body["threshold"] == 20
    assert body["expires"] == "2026-11-05T07:59:00Z"


@pytest.mark.unit
def test_directives_drain_reports_drained_at_threshold(client: TestClient) -> None:
    directive = Directive(
        id="dir-gm",
        text="Drain mode on Gasification_Model until 2026-11-05 07:59Z",
        set_by="operator",
        repo="Gasification_Model",
        expires="2026-11-05T07:59:00Z",
    )
    now = datetime(2026, 11, 3, 14, 0, tzinfo=UTC)
    with (
        patch("routers.directives_drain.get_directives") as mock_list,
        patch(
            "routers.directives_drain.fetch_open_non_draft_pr_count",
            new=AsyncMock(return_value=(20, None)),
        ),
        patch("routers.directives_drain.datetime") as mock_dt,
    ):
        mock_list.return_value.active.return_value = [directive]
        mock_dt.now.return_value = now
        res = client.get("/api/directives/drain", params={"repo": "Gasification_Model"})
    assert res.status_code == 200
    assert res.json()["drained"] is True
