"""Tests for Code Requests API and Feature Requests back-compat aliases (CR-1, #1281)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from identity import Principal, principal_has_scope  # noqa: E402
from routers import code_requests  # noqa: E402
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
    "prompt": "implement code request X",
}


@pytest.fixture
def cr_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    code_path = tmp_path / "code_requests.json"
    feat_path = tmp_path / "feature_requests.json"
    migr_path = tmp_path / "feature_requests.json.migrated"
    templates_path = tmp_path / "prompt_templates.json"
    notes_path = tmp_path / "prompt_notes.json"

    monkeypatch.setattr(code_requests, "_CODE_REQUESTS_PATH", code_path)
    monkeypatch.setattr(code_requests, "_LEGACY_FEATURE_REQUESTS_PATH", feat_path)
    monkeypatch.setattr(code_requests, "_MIGRATED_MARKER_PATH", migr_path)
    monkeypatch.setattr(code_requests, "_PROMPT_TEMPLATES_PATH", templates_path)
    monkeypatch.setattr(code_requests, "_PROMPT_NOTES_PATH", notes_path)
    monkeypatch.setattr(code_requests, "_dispatch_target_state", {"checked_at": None, "available": None, "detail": ""})
    return code_path, feat_path, migr_path


@pytest.fixture
def client(mock_auth: object, cr_env: tuple[Path, Path, Path]) -> TestClient:  # noqa: ARG001
    return TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})


def test_scope_presets_and_aliases() -> None:
    """code-requests.manage and feature-requests.manage mutually alias each other."""
    # Principal with code-requests.manage
    p_code = Principal(id="agent-code", type="bot", name="Code", scopes=["code-requests.manage"])
    assert principal_has_scope(p_code, "code-requests.manage")
    assert principal_has_scope(p_code, "feature-requests.manage")

    # Principal with feature-requests.manage
    p_feat = Principal(id="agent-feat", type="bot", name="Feat", scopes=["feature-requests.manage"])
    assert principal_has_scope(p_feat, "code-requests.manage")
    assert principal_has_scope(p_feat, "feature-requests.manage")

    # Operator role has both
    p_op = Principal(id="op", type="human", name="Operator", roles=["operator"])
    assert principal_has_scope(p_op, "code-requests.manage")
    assert principal_has_scope(p_op, "feature-requests.manage")


def test_storage_migration_idempotent(cr_env: tuple[Path, Path, Path]) -> None:
    """Migrates feature_requests.json to code_requests.json on first read, leaving .migrated marker."""
    code_path, feat_path, migr_path = cr_env
    initial_data = [{"id": "1", "repository": "Runner_Dashboard", "prompt": "Legacy"}]
    feat_path.write_text(json.dumps(initial_data), encoding="utf-8")

    assert not code_path.exists()
    assert not migr_path.exists()

    # First migration
    code_requests._migrate_storage_if_needed()
    assert code_path.exists()
    assert migr_path.exists()
    assert feat_path.exists(), "Original file must never be deleted"
    assert json.loads(code_path.read_text(encoding="utf-8")) == initial_data

    # Modify code_requests.json with newer data
    newer_data = initial_data + [{"id": "2", "repository": "Tools", "prompt": "Newer"}]
    code_path.write_text(json.dumps(newer_data), encoding="utf-8")

    # Subsequent migration must be an idempotent no-op
    code_requests._migrate_storage_if_needed()
    assert json.loads(code_path.read_text(encoding="utf-8")) == newer_data


def test_code_requests_endpoints(client: TestClient, cr_env: tuple[Path, Path, Path]) -> None:
    code_path, _, _ = cr_env
    data = [{"id": "100", "repository": "Runner_Dashboard", "prompt": "Test"}]
    code_path.write_text(json.dumps(data), encoding="utf-8")

    # List
    probe = AsyncMock(return_value=(0, "", ""))
    with patch("routers.code_requests.run_cmd", new=probe):
        resp = client.get("/api/code-requests")
    assert resp.status_code == 200
    res_data: dict[str, Any] = resp.json()
    assert res_data["total"] == 1
    assert res_data["requests"][0]["id"] == "100"
    assert res_data["dispatchTarget"]["available"] is True

    # Templates GET & POST
    t_get = client.get("/api/code-requests/templates")
    assert t_get.status_code == 200
    assert "standards" in t_get.json()

    t_post = client.post("/api/code-requests/templates", json={"name": "tpl-1", "content": "Template content"})
    assert t_post.status_code == 200
    assert t_post.json()["status"] == "saved"

    # Dispatch
    with patch("routers.code_requests.run_cmd", new=AsyncMock(return_value=(0, "", ""))):
        d_resp = client.post("/api/code-requests/dispatch", json=_BODY)
    assert d_resp.status_code == 200
    assert d_resp.json()["status"] == "dispatched"


def test_deprecated_feature_requests_endpoints(client: TestClient, cr_env: tuple[Path, Path, Path]) -> None:
    code_path, _, _ = cr_env
    data = [{"id": "200", "repository": "Runner_Dashboard", "prompt": "Deprecated"}]
    code_path.write_text(json.dumps(data), encoding="utf-8")

    # GET /api/feature-requests
    probe = AsyncMock(return_value=(0, "", ""))
    with patch("routers.code_requests.run_cmd", new=probe):
        resp = client.get("/api/feature-requests")
    assert resp.status_code == 200
    assert resp.headers.get("Deprecation") == "true"
    assert resp.headers.get("Link") == '</api/code-requests>; rel="successor-version"'
    res_data: dict[str, Any] = resp.json()
    assert res_data["total"] == 1

    # GET /api/feature-requests/templates
    t_get = client.get("/api/feature-requests/templates")
    assert t_get.status_code == 200
    assert t_get.headers.get("Deprecation") == "true"
    assert t_get.headers.get("Link") == '</api/code-requests/templates>; rel="successor-version"'

    # POST /api/feature-requests/templates
    t_post = client.post("/api/feature-requests/templates", json={"name": "tpl-dep", "content": "content"})
    assert t_post.status_code == 200
    assert t_post.headers.get("Deprecation") == "true"
    assert t_post.headers.get("Link") == '</api/code-requests/templates>; rel="successor-version"'

    # POST /api/feature-requests/dispatch
    with patch("routers.code_requests.run_cmd", new=AsyncMock(return_value=(0, "", ""))):
        d_resp = client.post("/api/feature-requests/dispatch", json=_BODY)
    assert d_resp.status_code == 200
    assert d_resp.headers.get("Deprecation") == "true"
    assert d_resp.headers.get("Link") == '</api/code-requests/dispatch>; rel="successor-version"'
    assert d_resp.json()["status"] == "dispatched"
