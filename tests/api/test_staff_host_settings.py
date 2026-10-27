"""Host settings API (issue #3101)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from routers import staff_host_settings
from staff import holds as holds_mod
from staff import host_settings_service as host_settings_service_mod
from staff.host_settings_contract import HOST_SETTINGS_PATH_ENV


OPERATOR = Principal(
    id="operator-test",
    type="human",
    name="Test",
    roles=["operator"],
    scopes=["staff.read", "staff.holds.write"],
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    settings_file = tmp_path / "host_settings.json"
    holds_file = tmp_path / "holds.json"
    monkeypatch.setenv(HOST_SETTINGS_PATH_ENV, str(settings_file))
    monkeypatch.setenv("STAFF_HOLDS_FILE", str(holds_file))
    holds = holds_mod.HoldsList(holds_file, roles_loader=dict)
    holds.replace([{"text": "no bulk stale-queue cancel", "applies_to": ["night-watch"]}])
    audits: list[dict[str, Any]] = []
    monkeypatch.setattr(host_settings_service_mod, "record_audit", lambda **kw: audits.append(kw) or 1)
    monkeypatch.setattr(
        staff_host_settings,
        "load_roles",
        lambda: {"barb": object(), "night-watch": object()},
    )
    app = FastAPI()
    app.include_router(staff_host_settings.router, prefix="/api/staff")
    app.dependency_overrides[require_scope("staff.read")] = lambda: OPERATOR
    app.dependency_overrides[require_scope("staff.holds.write")] = lambda: OPERATOR
    with TestClient(app, raise_server_exceptions=False) as c:
        c.audits = audits  # type: ignore[attr-defined]
        c.holds = holds  # type: ignore[attr-defined]
        yield c


@pytest.mark.unit
def test_get_host_settings_defaults_grokbot_first(client: TestClient) -> None:
    res = client.get("/api/staff/host-settings")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["settings"]["global"]["preference"] == "grokbot-first"
    leaders = {row["role"]: row["leader"] for row in body["resolved"]}
    assert leaders == {"barb": "grokbot", "night-watch": "grokbot"}


@pytest.mark.unit
def test_put_without_confirmation_is_rejected(client: TestClient) -> None:
    body = {
        "global": {"preference": "rd-first"},
        "roles": {},
        "confirmed": False,
    }
    res = client.put("/api/staff/host-settings", json=body)
    assert res.status_code == 409, res.text


@pytest.mark.unit
def test_put_flip_writes_audit_and_preserves_holds(client: TestClient) -> None:
    before = [h.to_dict() for h in client.holds.load()]  # type: ignore[attr-defined]
    body = {
        "global": {"preference": "grokbot-first"},
        "roles": {"barb": {"preference": "rd-first"}},
        "confirmed": True,
    }
    res = client.put("/api/staff/host-settings", json=body)
    assert res.status_code == 200, res.text
    assert client.audits[-1]["action"] == "host_flip"  # type: ignore[attr-defined]
    after = [h.to_dict() for h in client.holds.load()]  # type: ignore[attr-defined]
    assert before == after
    leaders = {row["role"]: row["leader"] for row in res.json()["resolved"]}
    assert leaders["barb"] == "rd"


@pytest.mark.unit
def test_put_invalid_preference_returns_422(client: TestClient) -> None:
    res = client.put(
        "/api/staff/host-settings",
        json={"global": {"preference": "invalid"}, "confirmed": True},
    )
    assert res.status_code == 422, res.text
