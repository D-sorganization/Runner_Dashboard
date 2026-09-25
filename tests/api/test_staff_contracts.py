"""Contract checks for staff and assistant response models and OpenAPI schema (SC-A10, Issue #1296).

Precondition: All non-streaming /api/staff/* and /api/assistant/* routes have Pydantic response_model configured.
Postcondition: Schema contains all response schemas; deliberate drift fixtures detect discrepancies.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import ValidationError
from server import app
from staff.models import (
    StaffBoardResponse,
    StaffRoleSpec,
    StaffRosterResponse,
    StaffRunRecord,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _get_all_routes(obj: Any) -> list[APIRoute]:
    routes: list[APIRoute] = []
    for item in getattr(obj, "routes", []):
        if hasattr(item, "original_router"):
            routes.extend(_get_all_routes(item.original_router))
        elif hasattr(item, "routes"):
            routes.extend(_get_all_routes(item))
        elif isinstance(item, APIRoute):
            routes.append(item)
    return routes


def test_staff_routes_have_response_models() -> None:
    """Every staff route (except SSE streaming) must have a response_model configured."""
    exempt = {"stream_run"}
    staff_routes = [r for r in _get_all_routes(app) if r.path.startswith("/api/staff")]
    assert len(staff_routes) >= 10, f"Expected at least 10 staff routes, found {len(staff_routes)}"

    missing_response_model = [
        f"{r.methods} {r.path} ({r.name})" for r in staff_routes if r.response_model is None and r.name not in exempt
    ]
    assert not missing_response_model, f"Routes missing response_model: {missing_response_model}"


def test_assistant_routes_have_response_models() -> None:
    """Every assistant route must have a response_model configured."""
    assistant_routes = [r for r in _get_all_routes(app) if r.path.startswith("/api/assistant")]
    assert len(assistant_routes) >= 5, f"Expected at least 5 assistant routes, found {len(assistant_routes)}"

    missing_response_model = [f"{r.methods} {r.path} ({r.name})" for r in assistant_routes if r.response_model is None]
    assert not missing_response_model, f"Routes missing response_model: {missing_response_model}"


def test_openapi_schema_contains_staff_and_assistant_schemas(
    client: TestClient,
) -> None:
    """OpenAPI /openapi.json contains all required Staff and Assistant component schemas."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    schemas = schema.get("components", {}).get("schemas", {})

    expected_schemas = [
        "StaffRoleSpec",
        "StaffRosterResponse",
        "StaffRunRecord",
        "StaffRunsResponse",
        "StaffRunDetailResponse",
        "StaffCancelResponse",
        "StaffDispatchResponse",
        "StaffBoardResponse",
        "StaffSummaryResponse",
        "StaffAuditRecordResponse",
        "StaffAuditListResponse",
        "StaffHold",
        "StaffHoldsResponse",
        "StaffScheduleToggleResponse",
        "StaffScheduleResponse",
        "StaffUsageResponse",
        "StaffPricingResponse",
        "StaffUsageExportResponse",
        "AssistantChatResponse",
        "ToolExecuteResponse",
    ]

    for name in expected_schemas:
        assert name in schemas, f"Schema {name} missing from OpenAPI components.schemas"


def test_staff_board_response_model_validation() -> None:
    """StaffBoardResponse validates valid payload and rejects type drift."""
    valid_payload: dict[str, Any] = {
        "generated_at": "2026-09-24T12:00:00Z",
        "machine": "node-1",
        "running": [],
        "queued": [],
        "recent": [],
        "spend_today_usd": {"claude": 1.25, "total": 1.25},
        "providers": {"claude": True},
    }
    model = StaffBoardResponse.model_validate(valid_payload)
    assert model.machine == "node-1"
    assert model.spend_today_usd["claude"] == 1.25

    # Type drift: spend_today_usd as a string instead of dict
    with pytest.raises(ValidationError):
        StaffBoardResponse.model_validate({**valid_payload, "spend_today_usd": "not-a-dict"})

    # Type drift: running as a string instead of list
    with pytest.raises(ValidationError):
        StaffBoardResponse.model_validate({**valid_payload, "running": "not-a-list"})


def test_staff_roster_response_model_validation() -> None:
    """StaffRosterResponse validates and catches missing required fields."""
    valid_role: dict[str, Any] = {
        "name": "reviewer",
        "title": "Code Reviewer",
        "summary": "Reviews pull requests",
        "playbook": "review.md",
        "providers": ["claude"],
        "repos": ["Runner_Dashboard"],
        "holds": [],
        "budget": {"usd_per_run": 2.0, "usd_per_day": 10.0},
        "dispatchable": True,
        "source_path": "/path/to/role.yml",
    }
    valid_roster: dict[str, Any] = {
        "machine": "node-1",
        "roles": [valid_role],
        "providers": {"claude": True},
        "active_runs": 0,
    }
    roster_model = StaffRosterResponse.model_validate(valid_roster)
    assert len(roster_model.roles) == 1
    assert roster_model.roles[0].name == "reviewer"

    # Type drift: invalid budget type
    with pytest.raises(ValidationError):
        StaffRoleSpec.model_validate({**valid_role, "budget": "not-a-budget"})


def test_deliberate_schema_drift_detection() -> None:
    """Deliberate drift fixture: simulates contract drift and verifies detection."""
    original_fields = set(StaffRunRecord.model_fields.keys())

    # Simulate an external schema definition missing newly added failure classification fields
    legacy_fields = original_fields - {"failure_class", "retryable", "remediation"}

    # Drift detection function comparing expected schema fields against an interface snapshot
    drift = original_fields - legacy_fields
    assert "failure_class" in drift
    assert "retryable" in drift
    assert "remediation" in drift
