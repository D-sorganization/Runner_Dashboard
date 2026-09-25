"""Feature Request dispatch must report the real outcome (issue #1280).

The dispatch target ``Jules-Feature-Request.yml`` does not exist in
Repository_Management, so ``gh api`` fails with HTTP 404.  The handler used to
swallow that failure, record the request as ``dispatched`` and return success,
silently dropping every request.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from routers import feature_requests  # noqa: E402
from server import app  # noqa: E402

_NOT_FOUND = (
    1,
    "",
    "gh: Not Found (HTTP 404)\nworkflow Jules-Feature-Request.yml not found on the default branch",
)
_BODY = {
    "repository": "Runner_Dashboard",
    "branch": "main",
    "provider": "jules_api",
    "prompt": "implement feature X",
}


@pytest.fixture
def history_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "feature_requests.json"
    monkeypatch.setattr(feature_requests, "_FEATURE_REQUESTS_PATH", path)
    monkeypatch.setattr(feature_requests, "_PROMPT_NOTES_PATH", tmp_path / "prompt_notes.json")
    monkeypatch.setattr(
        feature_requests, "_dispatch_target_state", {"checked_at": None, "available": None, "detail": ""}
    )
    monkeypatch.setattr(
        "agent_remediation.probe_provider_availability",
        lambda *a, **k: {
            "jules_api": type("_Avail", (), {"available": True, "detail": "ready"})(),
        },
    )
    return path


@pytest.fixture
def client(mock_auth, history_path: Path) -> TestClient:  # noqa: ARG001
    return TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})


def _history(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_dispatch_failure_returns_502_and_records_failed(client: TestClient, history_path: Path) -> None:
    with patch("routers.feature_requests.run_cmd", new=AsyncMock(return_value=_NOT_FOUND)):
        resp = client.post("/api/feature-requests/dispatch", json=_BODY)

    assert resp.status_code == 502
    assert "Jules-Feature-Request.yml" in resp.json()["detail"]
    entries = _history(history_path)
    assert len(entries) == 1
    assert entries[0]["status"] == "failed"
    assert "HTTP 404" in entries[0]["error"]


def test_dispatch_success_records_dispatched(client: TestClient, history_path: Path) -> None:
    with patch("routers.feature_requests.run_cmd", new=AsyncMock(return_value=(0, "", ""))):
        resp = client.post("/api/feature-requests/dispatch", json=_BODY)

    assert resp.status_code == 200
    assert resp.json()["status"] == "dispatched"
    entries = _history(history_path)
    assert entries[0]["status"] == "dispatched"
    assert "error" not in entries[0]


def test_list_reports_unavailable_dispatch_target(client: TestClient) -> None:
    probe = AsyncMock(return_value=_NOT_FOUND)
    with patch("routers.feature_requests.run_cmd", new=probe):
        first = client.get("/api/feature-requests").json()
        second = client.get("/api/feature-requests").json()

    assert first["dispatchTarget"]["available"] is False
    assert "Jules-Feature-Request.yml" in first["dispatchTarget"]["detail"]
    assert second["dispatchTarget"]["available"] is False
    assert probe.await_count == 1, "probe result should be cached between list calls"


def test_dispatch_failure_marks_target_unavailable(client: TestClient) -> None:
    calls = AsyncMock(return_value=_NOT_FOUND)
    with patch("routers.feature_requests.run_cmd", new=calls):
        client.post("/api/feature-requests/dispatch", json=_BODY)
        listed = client.get("/api/feature-requests").json()

    assert listed["dispatchTarget"]["available"] is False
    assert calls.await_count == 1, "a failed dispatch should prime the cache, not trigger another probe"
