"""Desk staff role dry-run self-test endpoints (#3847)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from identity import Principal
from staff.dispatch_auth import require_staff_run_dispatch
from staff.role_self_test import run_desk_core_self_tests, run_role_self_test

router = APIRouter(prefix="/api/staff", tags=["staff"])


@router.post("/{role}/self-test")
async def self_test_role(
    role: str,
    persona_drill: bool = Query(default=False, description="Also run the isolated #3704 persona fallback drill."),
    caller: Principal = Depends(require_staff_run_dispatch()),  # noqa: B008
) -> dict[str, Any]:
    """Dry-run dispatch for one role; optional persona drill uses ephemeral stores only."""
    return await run_role_self_test(role, caller, include_persona_drill=persona_drill)


@router.post("/self-test/desk-core")
async def self_test_desk_core(
    persona_drill: bool = Query(default=False),
    caller: Principal = Depends(require_staff_run_dispatch()),  # noqa: B008
) -> dict[str, Any]:
    """Run dry-run self-tests for every configured Desk core staff role."""
    return await run_desk_core_self_tests(caller, include_persona_drill=persona_drill)
