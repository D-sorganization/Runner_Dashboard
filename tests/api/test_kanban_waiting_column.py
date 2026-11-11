"""API tests for read-only Waiting-on-Dieter kanban column (#4321)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from coordination_fake_rm import FakeRM, bot, install
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from server import app
from staff.holds import Hold, HoldsList

RD = "D-sorganization/Runner_Dashboard"

READER = Principal(id="agent-cursor", type="bot", name="Cursor", roles=[], scopes=["staff.read"])


def _schedule_hold(hold_id: str, text: str) -> Hold:
    return Hold(id=hold_id, text=text, applies_to=["repo:Runner_Dashboard"], active=True, kind="schedule")


@pytest.fixture(autouse=True)
def kanban_auth() -> Iterator[None]:
    app.dependency_overrides[require_principal] = lambda: READER
    app.dependency_overrides[require_scope("staff.read")] = lambda: READER
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    principals = {"bot-token": bot("agent-cursor")}
    monkeypatch.setenv("STAFF_CONVERSATIONS_DB", str(tmp_path / "conv.db"))
    yield from install(tmp_path, monkeypatch, principals, repos="Runner_Dashboard")


@pytest.fixture
def client(rm: FakeRM) -> TestClient:
    return TestClient(app, base_url="http://testserver")


def test_kanban_router_has_no_mutating_methods() -> None:
    from routers import kanban as mod  # noqa: PLC0415

    for route in mod.router.routes:
        methods = set(route.methods or ())
        assert methods <= {"GET", "HEAD", "OPTIONS"}, methods


def test_unreadable_holds_column_is_unknown(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STAFF_HOLDS_FILE", str(tmp_path / "bad_holds.json"))
    path = tmp_path / "bad_holds.json"
    path.write_text("{bad", encoding="utf-8")

    res = client.get("/api/v1/kanban/columns/waiting-on-dieter")
    assert res.status_code == 200
    body = res.json()
    assert body["column"] == "unknown"
    assert body["holds_readable"] is False
    assert body["items"] == []
    assert "holds list unreadable" in body["reasons"]


def test_active_hold_surfaces_in_column(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from coordination import claims as claims_mod  # noqa: PLC0415

    monkeypatch.setenv("STAFF_HOLDS_FILE", str(tmp_path / "staff_holds.json"))
    HoldsList(path=tmp_path / "staff_holds.json").replace(
        [_schedule_hold("rd-2657", "no deploy until Monday").to_dict()]
    )

    def fetch(path: str) -> Any:
        if path == f"/repos/{RD}/issues?state=open&per_page=100":
            return [
                {
                    "number": 50,
                    "state": "open",
                    "html_url": f"https://github.com/{RD}/issues/50",
                    "labels": [{"name": "hold:rd-2657"}],
                    "title": "held issue",
                }
            ]
        if path == f"/repos/{RD}/pulls?state=open&per_page=100":
            return []
        return []

    monkeypatch.setattr(claims_mod, "evidence_fetch", fetch)

    body = client.get("/api/v1/kanban/columns/waiting-on-dieter").json()
    assert body["column"] == "waiting_on_dieter"
    assert body["holds_readable"] is True
    kinds = {item["source_kind"] for item in body["items"]}
    assert "hold" in kinds
    assert "issue" in kinds
    issue = next(i for i in body["items"] if i["source_kind"] == "issue")
    assert issue["card"]["column"] == "waiting_on_dieter"
    assert issue["source_url"].endswith("/issues/50")
