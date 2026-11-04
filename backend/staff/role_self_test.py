"""Dry-run dispatch self-tests for Desk staff roles (#3852)."""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import HTTPException
from identity import Principal
from staff.dispatch_service import DispatchCommand, dispatch_staff_run
from staff.runner import StaffRunner, get_runner

log = logging.getLogger("dashboard.staff.role_self_test")

_DEFAULT_DESK_CORE: tuple[str, ...] = (
    "bob",
    "night-watch",
    "issue-remediator",
    "pr-remediator",
    "cartographer",
    "board-secretary",
    "sanitation",
)


def desk_core_staff_roles() -> tuple[str, ...]:
    raw = os.environ.get("STAFF_DESK_CORE_ROLES", "").strip()
    if not raw:
        return _DEFAULT_DESK_CORE
    parts = tuple(r.strip() for r in raw.split(",") if r.strip())
    return parts or _DEFAULT_DESK_CORE


SELF_TEST_PROMPT = (
    "Self-test only: validate that this role can plan a staff run. Do not push, open a pull request, or merge."
)
SELF_TEST_REQUESTED_BY = "staff-self-test"


def _default_repo(spec_repos: list[str]) -> str:
    if "Runner_Dashboard" in spec_repos:
        return "Runner_Dashboard"
    return spec_repos[0] if spec_repos else "Runner_Dashboard"


async def run_role_self_test(
    role: str,
    caller: Principal,
    *,
    runner: StaffRunner | None = None,
    include_persona_drill: bool = False,
) -> dict[str, Any]:
    del include_persona_drill  # slice 2 (#3852)
    runner = runner or get_runner()
    spec = runner.roles().get(role)
    if spec is None:
        return {"role": role, "ok": False, "error": "unknown role"}
    if not spec.dispatchable:
        return {"role": role, "ok": False, "error": "role is not dispatchable"}

    repo = _default_repo(list(spec.repos))
    cmd = DispatchCommand(
        role=role,
        requested_by=SELF_TEST_REQUESTED_BY,
        repo=repo,
        prompt=SELF_TEST_PROMPT,
        dry_run=True,
        surface="self_test",
    )
    out: dict[str, Any] = {"role": role, "repo": repo, "checks": {}}
    try:
        plan = await dispatch_staff_run(cmd, caller)
        out["checks"]["dispatch_dry_run"] = {"ok": True, "plan": plan.get("plan")}
    except HTTPException as exc:
        out["checks"]["dispatch_dry_run"] = {"ok": False, "status": exc.status_code, "detail": exc.detail}
    except Exception as exc:  # noqa: BLE001
        log.exception("staff self-test dispatch failed for %s", role)
        out["checks"]["dispatch_dry_run"] = {"ok": False, "detail": str(exc)}

    out["ok"] = all(bool(c.get("ok")) for c in out["checks"].values())
    return out


async def run_desk_core_self_tests(
    caller: Principal,
    *,
    include_persona_drill: bool = False,
    runner: StaffRunner | None = None,
) -> dict[str, Any]:
    del include_persona_drill
    roles = desk_core_staff_roles()
    results = [await run_role_self_test(r, caller, runner=runner, include_persona_drill=False) for r in roles]
    return {
        "roles": list(roles),
        "ok": all(r.get("ok") for r in results),
        "results": results,
    }
