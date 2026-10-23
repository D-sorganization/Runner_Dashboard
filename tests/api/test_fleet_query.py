"""Tests for unified fleet_query endpoint GET /api/v1/fleet/query (#2223)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_principal


@pytest.fixture
def env(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from routers import context_query
    from server import app

    context_query.clear_context_query_cache()
    app.dependency_overrides.clear()
    yield TestClient(app), app
    app.dependency_overrides.clear()


def _auth(app: FastAPI, bot: str = "agent-codex", scopes: list[str] | None = None) -> dict[str, str]:
    sc = ["fleet.read", "staff.read", "memory.read"] if scopes is None else scopes
    app.dependency_overrides[require_principal] = lambda: Principal(id=bot, type="bot", name="Agent", scopes=sc)
    return {"Authorization": f"Bearer tok-{bot}", "X-Requested-With": "XMLHttpRequest"}


def test_scope_enforcement_per_source(env: tuple[TestClient, FastAPI]) -> None:
    client, app = env
    assert (
        client.get("/api/v1/fleet/query?source=library", headers=_auth(app, "b1", ["memory.read"])).status_code == 403
    )
    assert (
        client.get("/api/v1/fleet/query?source=knowledge", headers=_auth(app, "b2", ["fleet.read"])).status_code == 403
    )
    assert client.get("/api/v1/fleet/query?source=memory", headers=_auth(app, "b3", ["fleet.read"])).status_code == 403


def test_caps_enforced_even_with_full(env: tuple[TestClient, FastAPI]) -> None:
    client, app = env
    r = client.get("/api/v1/fleet/query?source=context&full=true&limit=100", headers=_auth(app, "bot-full"))
    assert r.status_code == 200
    data = r.json()
    assert len(data.get("items", [])) <= 5
    for item in data.get("items", []):
        assert len(item.get("snippet", "")) <= 400


def test_barb_only_excluded(env: tuple[TestClient, FastAPI]) -> None:
    client, app = env
    client.post(
        "/api/v1/memory",
        json={
            "entry_type": "decision",
            "source_product": "codex",
            "body": {"summary": "Secret Barb lesson", "visibility": "barb-only"},
        },
        headers=_auth(app, "writer", ["memory.write"]),
    )
    r = client.get("/api/v1/fleet/query?source=memory&query=Secret", headers=_auth(app, "bot-mem"))
    assert r.status_code == 200
    assert not any("Secret Barb lesson" in item.get("snippet", "") for item in r.json().get("items", []))


def test_offline_hub_returns_unavailable_string(
    env: tuple[TestClient, FastAPI], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, app = env
    from routers import staff_schedule

    monkeypatch.setattr(staff_schedule, "get_scheduler", lambda: (_ for _ in ()).throw(RuntimeError("Hub offline")))
    r = client.get("/api/v1/fleet/query?source=context", headers=_auth(app, "bot-ctx"))
    assert r.status_code == 200
    assert r.json().get("context", {}).get("holds") == "unavailable"


def test_context_caching_and_invalidation(env: tuple[TestClient, FastAPI]) -> None:
    client, app = env
    from routers import context_query

    h = _auth(app, "bot-cache")
    r1 = client.get("/api/v1/fleet/query?source=context&repo=Runner_Dashboard", headers=h).json()
    assert r1.get("cached") is False
    r2 = client.get("/api/v1/fleet/query?source=context&repo=Runner_Dashboard", headers=h).json()
    assert r2.get("cached") is True
    context_query.clear_context_query_cache()
    r3 = client.get("/api/v1/fleet/query?source=context&repo=Runner_Dashboard", headers=h).json()
    assert r3.get("cached") is False
