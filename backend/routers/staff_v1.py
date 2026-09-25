"""Staff API v1 router (SC-A10, issue #1296).

Mirrors /api/staff/* endpoints under the new /api/v1/staff/* path with identical
Pydantic contracts and handlers for forward compatibility across agent clients.
"""

from __future__ import annotations

from fastapi import APIRouter
from routers import staff as _staff
from routers import staff_schedule as _schedule
from routers import staff_usage as _usage
from routers.staff_models import (
    StaffAuditResponse,
    StaffBoardResponse,
    StaffCancelResponse,
    StaffDispatchResponse,
    StaffExportUsageResponse,
    StaffHoldsResponse,
    StaffPricingResponse,
    StaffRosterResponse,
    StaffRunDetailResponse,
    StaffRunsResponse,
    StaffScheduleResponse,
    StaffSummaryResponse,
    StaffToggleScheduleResponse,
    StaffUsageResponse,
)

router = APIRouter(prefix="/api/v1/staff", tags=["staff-v1"])

# Roster & Roles
router.add_api_route("/roster", _staff.roster, methods=["GET"], response_model=StaffRosterResponse)
router.add_api_route("/roles", _staff.roster, methods=["GET"], response_model=StaffRosterResponse)

# Board and Summary
router.add_api_route(
    "/board",
    _staff.board,
    methods=["GET"],
    response_model=StaffBoardResponse,
    response_model_exclude_none=True,
)
router.add_api_route(
    "/summary",
    _staff.summary,
    methods=["GET"],
    response_model=StaffSummaryResponse,
    response_model_exclude_none=True,
)

# Audit
router.add_api_route("/audit", _staff.list_audit, methods=["GET"], response_model=StaffAuditResponse)

# Runs
router.add_api_route("/runs", _staff.list_runs, methods=["GET"], response_model=StaffRunsResponse)
router.add_api_route("/runs/{run_id}", _staff.get_run, methods=["GET"], response_model=StaffRunDetailResponse)
router.add_api_route("/runs/{run_id}/stream", _staff.stream_run, methods=["GET"])
router.add_api_route("/runs/{run_id}/cancel", _staff.cancel_run, methods=["POST"], response_model=StaffCancelResponse)
router.add_api_route(
    "/{role}/run",
    _staff.dispatch,
    methods=["POST"],
    response_model=StaffDispatchResponse,
    response_model_exclude_unset=True,
)

# Holds & Schedule
router.add_api_route("/holds", _schedule.get_holds, methods=["GET"], response_model=StaffHoldsResponse)
router.add_api_route("/holds", _schedule.put_holds, methods=["PUT"], response_model=StaffHoldsResponse)
router.add_api_route(
    "/schedule/toggle",
    _schedule.toggle_scheduler,
    methods=["POST"],
    response_model=StaffToggleScheduleResponse,
)
router.add_api_route("/schedule", _schedule.get_schedule, methods=["GET"], response_model=StaffScheduleResponse)

# Usage
router.add_api_route("/usage", _usage.get_usage, methods=["GET"], response_model=StaffUsageResponse)
router.add_api_route("/usage/pricing", _usage.get_pricing, methods=["GET"], response_model=StaffPricingResponse)
router.add_api_route("/usage/export", _usage.export_usage, methods=["POST"], response_model=StaffExportUsageResponse)
