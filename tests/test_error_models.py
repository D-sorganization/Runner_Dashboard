"""Tests for the shared ErrorResponse model and factory helpers (issue #406)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))


def test_error_response_shape() -> None:
    """ErrorResponse must produce the canonical envelope."""
    from error_models import ErrorResponse

    resp = ErrorResponse(error="not_found", detail="runner 99 not found")
    payload = resp.model_dump(exclude_none=True)
    assert payload == {"error": "not_found", "detail": "runner 99 not found"}


def test_error_response_with_request_id() -> None:
    """request_id field is included when provided."""
    from error_models import ErrorResponse

    resp = ErrorResponse(error="server_error", detail="boom", request_id="abc-123")
    payload = resp.model_dump(exclude_none=True)
    assert payload["request_id"] == "abc-123"


def test_error_response_excludes_none_request_id() -> None:
    """request_id is absent when not set."""
    from error_models import ErrorResponse

    resp = ErrorResponse(error="not_found", detail="missing")
    payload = resp.model_dump(exclude_none=True)
    assert "request_id" not in payload


def test_factory_not_found() -> None:
    from error_models import not_found

    r = not_found("runner 42 not found")
    assert r.error == "not_found"
    assert "42" in r.detail


def test_factory_validation_error() -> None:
    from error_models import validation_error

    r = validation_error("workflow_name is required")
    assert r.error == "validation_error"


def test_factory_bad_gateway() -> None:
    from error_models import bad_gateway

    r = bad_gateway("upstream timeout")
    assert r.error == "bad_gateway"


def test_factory_rate_limited() -> None:
    from error_models import rate_limited

    r = rate_limited("GitHub rate limited; retry after 60s")
    assert r.error == "rate_limited"


def test_factory_service_error_default_code() -> None:
    from error_models import service_error

    r = service_error("Failed to start runner 3: stderr output")
    assert r.error == "service_error"


def test_extra_fields_forbidden() -> None:
    """ErrorResponse rejects extra fields (extra='forbid')."""
    from error_models import ErrorResponse
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ErrorResponse(error="x", detail="y", unexpected_key="z")  # type: ignore[call-arg]


def test_runners_router_imports_error_models() -> None:
    """runners.py must import from error_models (including service_stderr_to_status)."""
    source = (_BACKEND_DIR / "routers" / "runners.py").read_text(encoding="utf-8")
    assert "from error_models import" in source
    assert "service_stderr_to_status" in source


def test_queue_router_imports_error_models() -> None:
    """queue.py must import from error_models."""
    source = (_BACKEND_DIR / "routers" / "queue.py").read_text(encoding="utf-8")
    assert "from error_models import" in source


def test_runs_workflows_router_imports_error_models() -> None:
    """runs_workflows.py must import from error_models."""
    source = (_BACKEND_DIR / "routers" / "runs_workflows.py").read_text(encoding="utf-8")
    assert "from error_models import" in source


def test_service_error_status_not_loaded() -> None:
    """service_stderr_to_status maps 'not loaded' stderr to 404."""
    from error_models import service_stderr_to_status

    assert service_stderr_to_status("Unit not found or not loaded") == 404


def test_service_error_status_permission_denied() -> None:
    """service_stderr_to_status maps 'permission denied' to 403."""
    from error_models import service_stderr_to_status

    assert service_stderr_to_status("Failed to start: permission denied") == 403


def test_service_error_status_generic() -> None:
    """service_stderr_to_status maps unknown stderr to 500."""
    from error_models import service_stderr_to_status

    assert service_stderr_to_status("Some random error occurred") == 500
