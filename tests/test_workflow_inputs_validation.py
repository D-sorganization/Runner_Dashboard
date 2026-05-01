"""Tests for workflow_dispatch input validation (issue #411).

Covers the size and type cap enforced before any I/O is performed by the
``/api/workflows/dispatch`` and ``/api/feature-requests/dispatch`` handlers.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from input_validation import (  # noqa: E402
    MAX_INPUT_KEYS,
    MAX_INPUT_VALUE_LENGTH,
    validate_workflow_inputs,
)
from server import app  # noqa: E402

# ─── Unit tests for the helper ────────────────────────────────────────────────


def test_validate_clean_inputs_returns_dict() -> None:
    inputs = {"foo": "bar", "baz": "qux"}
    out = validate_workflow_inputs(inputs)
    assert out == inputs


def test_validate_empty_inputs_ok() -> None:
    assert validate_workflow_inputs({}) == {}


def test_validate_none_returns_empty() -> None:
    assert validate_workflow_inputs(None) == {}


def test_validate_rejects_non_mapping() -> None:
    with pytest.raises(HTTPException) as exc:
        validate_workflow_inputs(["not", "a", "dict"])
    assert exc.value.status_code == 400


def test_validate_rejects_too_many_keys() -> None:
    inputs = {f"k{i}": "v" for i in range(MAX_INPUT_KEYS + 1)}
    with pytest.raises(HTTPException) as exc:
        validate_workflow_inputs(inputs)
    assert exc.value.status_code == 400
    assert "too many" in str(exc.value.detail).lower() or "keys" in str(exc.value.detail).lower()


def test_validate_rejects_oversized_value() -> None:
    inputs = {"prompt": "x" * (MAX_INPUT_VALUE_LENGTH + 1)}
    with pytest.raises(HTTPException) as exc:
        validate_workflow_inputs(inputs)
    assert exc.value.status_code == 400
    assert "length" in str(exc.value.detail).lower() or "long" in str(exc.value.detail).lower()


def test_validate_accepts_bool_value() -> None:
    """GitHub Actions workflow_dispatch supports `boolean` typed inputs."""
    out = validate_workflow_inputs({"flag": True})
    assert out == {"flag": "True"}


def test_validate_accepts_int_value() -> None:
    """GitHub Actions workflow_dispatch supports `number` typed inputs."""
    out = validate_workflow_inputs({"count": 5})
    assert out == {"count": "5"}


def test_validate_accepts_float_value() -> None:
    out = validate_workflow_inputs({"ratio": 1.5})
    assert out == {"ratio": "1.5"}


def test_validate_rejects_none_value() -> None:
    inputs = {"x": None}
    with pytest.raises(HTTPException) as exc:
        validate_workflow_inputs(inputs)
    assert exc.value.status_code == 400


def test_validate_rejects_list_value() -> None:
    """Containers (list/dict) are not valid workflow_dispatch input values."""
    with pytest.raises(HTTPException) as exc:
        validate_workflow_inputs({"items": ["a", "b"]})
    assert exc.value.status_code == 400


def test_validate_rejects_non_string_key() -> None:
    with pytest.raises(HTTPException) as exc:
        validate_workflow_inputs({1: "v"})
    assert exc.value.status_code == 400


def test_validate_value_at_exact_limit_ok() -> None:
    inputs = {"prompt": "x" * MAX_INPUT_VALUE_LENGTH}
    out = validate_workflow_inputs(inputs)
    assert out == inputs


def test_validate_keys_at_exact_limit_ok() -> None:
    inputs = {f"k{i}": "v" for i in range(MAX_INPUT_KEYS)}
    out = validate_workflow_inputs(inputs)
    assert out == inputs


# ─── Integration tests via /api/workflows/dispatch ────────────────────────────


@pytest.fixture
def client(mock_auth) -> TestClient:  # noqa: ARG001
    return TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})


def test_dispatch_oversized_value_returns_400(client: TestClient) -> None:
    body = {
        "repository": "Runner_Dashboard",
        "workflow_id": "ci.yml",
        "ref": "main",
        "inputs": {"prompt": "x" * (MAX_INPUT_VALUE_LENGTH + 1)},
    }
    resp = client.post("/api/workflows/dispatch", json=body)
    assert resp.status_code == 400


def test_dispatch_too_many_keys_returns_400(client: TestClient) -> None:
    body = {
        "repository": "Runner_Dashboard",
        "workflow_id": "ci.yml",
        "ref": "main",
        "inputs": {f"k{i}": "v" for i in range(MAX_INPUT_KEYS + 1)},
    }
    resp = client.post("/api/workflows/dispatch", json=body)
    assert resp.status_code == 400


def test_dispatch_unsupported_value_returns_400(client: TestClient) -> None:
    """Containers (list/dict) are rejected — workflow_dispatch supports only
    str/bool/number scalar values."""
    body = {
        "repository": "Runner_Dashboard",
        "workflow_id": "ci.yml",
        "ref": "main",
        "inputs": {"items": ["a", "b"]},
    }
    resp = client.post("/api/workflows/dispatch", json=body)
    assert resp.status_code == 400


def test_dispatch_clean_inputs_ok(client: TestClient) -> None:
    async def _ok_run(*_args, **_kwargs) -> tuple[int, str, str]:
        return 0, "", ""

    body = {
        "repository": "Runner_Dashboard",
        "workflow_id": "ci.yml",
        "ref": "main",
        "inputs": {"prompt": "hello world"},
    }
    with patch("routers.runs_workflows.run_cmd", new=AsyncMock(side_effect=_ok_run)):
        resp = client.post("/api/workflows/dispatch", json=body)
    assert resp.status_code in (200, 202)
    assert resp.json().get("status") == "dispatched"


# ─── Integration tests via /api/feature-requests/dispatch ─────────────────────


def test_feature_request_oversized_value_returns_400(client: TestClient) -> None:
    body = {
        "repository": "Runner_Dashboard",
        "branch": "main",
        "provider": "jules_api",
        "prompt": "ok",
        "inputs": {"target_repository": "x" * (MAX_INPUT_VALUE_LENGTH + 1)},
    }
    resp = client.post("/api/feature-requests/dispatch", json=body)
    assert resp.status_code == 400


def test_feature_request_too_many_keys_returns_400(client: TestClient) -> None:
    body = {
        "repository": "Runner_Dashboard",
        "branch": "main",
        "provider": "jules_api",
        "prompt": "ok",
        "inputs": {f"k{i}": "v" for i in range(MAX_INPUT_KEYS + 1)},
    }
    resp = client.post("/api/feature-requests/dispatch", json=body)
    assert resp.status_code == 400


def test_feature_request_unsupported_value_returns_400(client: TestClient) -> None:
    body = {
        "repository": "Runner_Dashboard",
        "branch": "main",
        "provider": "jules_api",
        "prompt": "ok",
        "inputs": {"nested": {"k": "v"}},
    }
    resp = client.post("/api/feature-requests/dispatch", json=body)
    assert resp.status_code == 400


def test_feature_request_clean_inputs_ok(client: TestClient) -> None:
    async def _ok_run(*_args, **_kwargs) -> tuple[int, str, str]:
        return 0, "", ""

    body = {
        "repository": "Runner_Dashboard",
        "branch": "main",
        "provider": "jules_api",
        "prompt": "implement feature X",
        "inputs": {"extra": "value"},
    }
    with patch("routers.feature_requests.run_cmd", new=AsyncMock(side_effect=_ok_run)):
        resp = client.post("/api/feature-requests/dispatch", json=body)
    assert resp.status_code in (200, 202)
    assert resp.json().get("status") == "dispatched"
