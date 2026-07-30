"""Consumer-driven Maxwell-Daemon OpenAPI contract checks.

These tests use the producer-owned OpenAPI snapshot vendored from
Maxwell_Daemon instead of hand-written dashboard fixtures. They intentionally
check only the fields Runner Dashboard consumes, so additive producer fields do
not create noisy failures while renamed required fields fail loudly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import maxwell_contract as mc  # noqa: E402

_OPENAPI_PATH = _ROOT / "tests" / "contracts" / "maxwell_openapi.json"

_RD_CONSUMED_PATHS = {
    "/api/connection-profile",
    "/api/dispatch",
    "/api/status",
    "/api/tasks",
    "/api/tasks/{task_id}",
    "/api/v1/cost",
    "/api/v1/workers",
    "/api/v2/status",
    "/api/version",
}

_MODEL_SCHEMAS: dict[str, type[BaseModel]] = {
    "VersionResponse": mc.MaxwellVersionResponse,
    "StatusResponse": mc.MaxwellStatusResponse,
    "StatusV2Response": mc.MaxwellStatusV2Response,
    "TaskListResponse": mc.MaxwellTaskListResponse,
    "TaskSummary": mc.MaxwellTaskItem,
    "TaskDetail": mc.MaxwellTaskDetailResponse,
    "CostSummary": mc.MaxwellCostResponse,
    "DispatchResponse": mc.MaxwellDispatchResponse,
}

_DRIFT_DISCRIMINATORS = {
    "CostSummary": "month_to_date_usd",
    "DispatchResponse": "task_id",
    "StatusResponse": "pipeline_state",
    "StatusV2Response": "counts",
    "TaskDetail": "status",
    "TaskListResponse": "total",
    "TaskSummary": "status",
    "VersionResponse": "contract",
}


def _openapi() -> dict[str, Any]:
    return json.loads(_OPENAPI_PATH.read_text(encoding="utf-8"))


def _schemas() -> dict[str, Any]:
    return _openapi()["components"]["schemas"]


def _resolve_ref(schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if not isinstance(ref, str):
        return schema
    prefix = "#/components/schemas/"
    if not ref.startswith(prefix):
        raise AssertionError(f"Unsupported OpenAPI ref: {ref}")
    return _schemas()[ref.removeprefix(prefix)]


def _sample(schema: dict[str, Any]) -> Any:
    schema = _resolve_ref(schema)
    if "anyOf" in schema:
        return _sample(schema["anyOf"][0])
    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        return {name: _sample(properties[name]) for name in required}
    if schema_type == "array":
        return [_sample(schema.get("items", {}))]
    if schema_type == "integer":
        return 1
    if schema_type == "number":
        return 1.0
    if schema_type == "boolean":
        return True
    return "value"


def test_vendored_openapi_snapshot_exposes_dashboard_consumed_paths() -> None:
    paths = set(_openapi()["paths"])

    assert paths >= _RD_CONSUMED_PATHS


@pytest.mark.parametrize("schema_name", sorted(_MODEL_SCHEMAS))
def test_vendored_openapi_snapshot_exposes_dashboard_consumed_schemas(schema_name: str) -> None:
    assert schema_name in _schemas()


@pytest.mark.parametrize(("schema_name", "model"), sorted(_MODEL_SCHEMAS.items()))
def test_dashboard_models_accept_producer_openapi_required_shape(
    schema_name: str,
    model: type[BaseModel],
) -> None:
    payload = _sample(_schemas()[schema_name])

    model.model_validate(payload)


@pytest.mark.parametrize(("schema_name", "model"), sorted(_MODEL_SCHEMAS.items()))
def test_dashboard_models_reject_required_producer_field_renames(
    schema_name: str,
    model: type[BaseModel],
) -> None:
    schema = _schemas()[schema_name]
    field = _DRIFT_DISCRIMINATORS[schema_name]
    assert field in schema.get("required", [])
    payload = _sample(schema)
    renamed = dict(payload)
    renamed[f"{field}_renamed"] = renamed.pop(field)

    with pytest.raises(ValidationError):
        model.model_validate(renamed)


def test_cost_summary_maps_producer_total_to_dashboard_total() -> None:
    payload = _sample(_schemas()["CostSummary"])

    result = mc.MaxwellCostResponse.model_validate(payload).model_dump()

    assert result["month_to_date_usd"] == payload["month_to_date_usd"]
    assert result["total_usd"] == payload["month_to_date_usd"]
