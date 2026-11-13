"""Tests for personal presentation preferences (#3495)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

ROLES_YAML_BARB = "name: barb\ntitle: Barb\nsummary: Fleet secretary.\n"
ROLES_YAML_CODEX = "name: codex-worker\ntitle: Codex Worker\nsummary: Lane worker.\n"


@pytest.fixture
def roles_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "barb.yml").write_text(ROLES_YAML_BARB)
    (roles / "codex-worker.yml").write_text(ROLES_YAML_CODEX)
    monkeypatch.setenv("STAFF_ROLES_DIR", str(roles))
    import staff.roles as roles_mod

    roles_mod.clear_roles_cache()
    yield roles
    roles_mod.clear_roles_cache()


@pytest.fixture
def prefs_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("STAFF_PRESENTATION_PREFS_DB", str(tmp_path / "staff_presentation_prefs.sqlite3"))
    import staff.presentation_prefs as prefs_mod

    prefs_mod._STORE = None
    yield
    prefs_mod._STORE = None


def _client_as(operator_id: str) -> Any:
    from fastapi.testclient import TestClient
    from identity import Principal, require_principal
    from server import app

    principal = Principal(id=operator_id, type="human", name=operator_id, scopes=["*"])
    app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(app, raise_server_exceptions=False, headers={"X-Requested-With": "XMLHttpRequest"})


@pytest.fixture
def client(roles_dir: Path, prefs_db: None) -> Any:
    c = _client_as("operator-a")
    try:
        yield c
    finally:
        from server import app

        app.dependency_overrides.clear()


def _put(c: Any, revision: int, **overrides: Any) -> Any:
    body = {"expected_revision": revision, "display_label": "", "role_order": [], "avatar_overrides": {}}
    body.update(overrides)
    return c.put("/api/v1/staff/presentation-prefs", json=body)


def test_missing_preferences_returns_defaults_without_error(client: Any) -> None:
    body = client.get("/api/v1/staff/presentation-prefs").json()
    assert body == {
        "operator_id": "operator-a",
        "display_label": "",
        "role_order": [],
        "avatar_overrides": {},
        "revision": 0,
        "updated_at": "",
    }


def test_save_survives_restart_and_second_client_read(client: Any) -> None:
    overrides = {"barb": "barb-alt.webp"}
    resp = _put(client, 0, display_label="Dieter", role_order=["codex-worker", "barb"], avatar_overrides=overrides)
    assert resp.status_code == 200
    assert resp.json()["revision"] == 1

    import staff.presentation_prefs as prefs_mod

    prefs_mod._STORE = None  # simulate a restart: drop the in-process store singleton
    body = client.get("/api/v1/staff/presentation-prefs").json()
    assert body["display_label"] == "Dieter"
    assert body["role_order"] == ["codex-worker", "barb"]
    assert body["avatar_overrides"] == {"barb": "barb-alt.webp"}
    assert body["revision"] == 1


def test_operator_isolation(roles_dir: Path, prefs_db: None) -> None:
    from server import app

    client_a = _client_as("operator-a")
    _put(client_a, 0, display_label="A's label")
    app.dependency_overrides.clear()

    client_b = _client_as("operator-b")
    body_b = client_b.get("/api/v1/staff/presentation-prefs").json()
    app.dependency_overrides.clear()

    assert body_b == {
        "operator_id": "operator-b",
        "display_label": "",
        "role_order": [],
        "avatar_overrides": {},
        "revision": 0,
        "updated_at": "",
    }


def test_unknown_role_id_rejected(client: Any) -> None:
    assert _put(client, 0, role_order=["not-a-real-role"]).status_code == 422


def test_duplicate_role_id_rejected(client: Any) -> None:
    assert _put(client, 0, role_order=["barb", "barb"]).status_code == 422


def test_unknown_role_id_in_avatar_overrides_rejected(client: Any) -> None:
    assert _put(client, 0, avatar_overrides={"ghost-role": "x.webp"}).status_code == 422


def test_stale_revision_conflict(client: Any) -> None:
    assert _put(client, 0, display_label="first").json()["revision"] == 1
    assert _put(client, 0, display_label="stale write").status_code == 409
    assert _put(client, 1, display_label="second").json()["revision"] == 2


def test_display_label_too_long_rejected(client: Any) -> None:
    assert _put(client, 0, display_label="x" * 201).status_code == 422


def test_reset_clears_back_to_defaults(client: Any) -> None:
    _put(client, 0, display_label="to be reset", role_order=["barb"])
    body = client.post("/api/v1/staff/presentation-prefs/reset").json()
    assert body["display_label"] == ""
    assert body["role_order"] == []
    assert body["revision"] == 0


def test_no_disallowed_fields_accepted(client: Any) -> None:
    resp = client.put(
        "/api/v1/staff/presentation-prefs",
        json={
            "expected_revision": 0,
            "display_label": "",
            "role_order": [],
            "avatar_overrides": {},
            "scopes": ["staff.admin"],
            "holds": ["2657"],
        },
    )
    assert resp.status_code == 422
