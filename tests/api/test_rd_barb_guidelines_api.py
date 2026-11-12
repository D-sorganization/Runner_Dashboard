"""GET /api/v1/staff/rd-barb/guidelines (RD #3065)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from routers import staff_rd_barb
from staff.redaction import redact_sensitive_content

_XHR = {"X-Requested-With": "XMLHttpRequest"}
URL = "/api/v1/staff/rd-barb/guidelines"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    application = FastAPI()
    application.include_router(staff_rd_barb.router)
    return application


def _as(app: FastAPI, scopes: list[str]) -> TestClient:
    prin = Principal(id="grok-barb", type="bot", name="Grok Barb", roles=[], scopes=scopes)
    app.dependency_overrides[require_principal] = lambda: prin
    return TestClient(app)


def test_requires_fleet_read_and_returns_doc_size_without_secrets(app: FastAPI) -> None:
    assert TestClient(app).get(URL, headers=_XHR).status_code in (401, 403)
    assert _as(app, []).get(URL, headers=_XHR).status_code == 403
    staff_rd_barb.guidelines_doc().add_procedure("Check queue depth before arming.")
    resp = _as(app, ["fleet.read"]).get(URL, headers=_XHR)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"doc", "size_bytes", "compacted_at"} and body["compacted_at"] is None
    assert "## Durable procedures" in body["doc"] and "Check queue depth before arming." in body["doc"]
    assert body["size_bytes"] == len(body["doc"].encode("utf-8")) <= 8192
    assert redact_sensitive_content(body["doc"]) == body["doc"]
