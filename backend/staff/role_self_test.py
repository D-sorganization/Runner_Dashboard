"""Dry-run dispatch self-tests for Desk staff roles (#3852)."""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from identity import Principal
from staff.barb_continuity import BarbContinuityStore
from staff.dispatch_service import DispatchCommand, dispatch_staff_run
from staff.role_capsule import RoleCapsuleStore
from staff.role_health import RoleHealthStore
from staff.runner import StaffRunner, get_runner
from staff.schedule import DEFAULT_TZ

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


def _noop_notify_opts(state_path: Path) -> dict[str, Any]:
    return {
        "config": {},
        "state_path": state_path,
        "sender": lambda _url, _hdrs, _msg: {"ok": True, "status": "shadow"},
    }


def _run_persona_drill(role: str, work_dir: Path) -> dict[str, Any]:
    from staff import (  # noqa: PLC0415
        board_secretary_persona,
        bob_persona,
        cartographer_persona,
        issue_remediator_persona,
        night_watch_persona,
        pr_remediator_persona,
        sanitation_persona,
    )

    la = datetime.now(ZoneInfo(DEFAULT_TZ))
    continuity = BarbContinuityStore(work_dir / "continuity.db")
    capsules = RoleCapsuleStore(work_dir / "capsules")
    health = RoleHealthStore(work_dir / "role-health.json")
    notify = _noop_notify_opts(work_dir / "notify-state.json")

    if role == "bob":
        return bob_persona.run_bob_fallback_drill(
            continuity,
            capsules,
            health,
            slot=f"{la.date().isoformat()}T17:00",
            scorecard_date=la.date().isoformat(),
            notify_opts=notify,
            base_time=la.replace(hour=16, minute=40, second=0, microsecond=0),
        )
    if role == "night-watch":
        return night_watch_persona.run_night_watch_fallback_drill(
            continuity,
            capsules,
            health,
            slot=f"{la.date().isoformat()}T22:00",
            overnight_date=la.date().isoformat(),
            notify_opts=notify,
            base_time=la.replace(hour=21, minute=50, second=0, microsecond=0),
        )
    if role == "issue-remediator":
        return issue_remediator_persona.run_issue_remediator_fallback_drill(
            continuity,
            capsules,
            health,
            slot=f"{la.date().isoformat()}T23:00",
            run_date=la.date().isoformat(),
            notify_opts=notify,
            base_time=la.replace(hour=22, minute=50, second=0, microsecond=0),
        )
    if role == "pr-remediator":
        return pr_remediator_persona.run_pr_remediator_fallback_drill(
            continuity,
            capsules,
            health,
            slot=f"{la.date().isoformat()}T23:30",
            run_date=la.date().isoformat(),
            notify_opts=notify,
            base_time=la.replace(hour=23, minute=20, second=0, microsecond=0),
        )
    if role == "cartographer":
        return cartographer_persona.run_cartographer_fallback_drill(
            continuity,
            capsules,
            health,
            slot=f"{la.date().isoformat()}T04:00",
            sweep_date=la.date().isoformat(),
            notify_opts=notify,
            base_time=la.replace(hour=3, minute=50, second=0, microsecond=0),
        )
    if role == "board-secretary":
        return board_secretary_persona.run_board_secretary_fallback_drill(
            continuity,
            capsules,
            health,
            slot=f"{la.date().isoformat()}T20:00",
            meeting_date=la.date().isoformat(),
            notify_opts=notify,
            base_time=la.replace(hour=19, minute=50, second=0, microsecond=0),
        )
    if role == "sanitation":
        return sanitation_persona.run_sanitation_fallback_drill(
            continuity,
            capsules,
            health,
            slot=f"{la.date().isoformat()}T04:30",
            sweep_date=la.date().isoformat(),
            notify_opts=notify,
            base_time=la.replace(hour=4, minute=20, second=0, microsecond=0),
        )
    raise ValueError(f"no persona drill registered for role {role!r}")


async def run_role_self_test(
    role: str,
    caller: Principal,
    *,
    runner: StaffRunner | None = None,
    include_persona_drill: bool = False,
) -> dict[str, Any]:
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

    if include_persona_drill and role in desk_core_staff_roles():
        try:
            with tempfile.TemporaryDirectory(prefix=f"staff-self-test-{role}-") as tmp:
                drill = _run_persona_drill(role, Path(tmp))
                rd_slot = drill.get("rd_slot_after_resume") or {}
                ok = isinstance(rd_slot, Mapping) and rd_slot.get("status") in {"skipped", "executed"}
                out["checks"]["persona_drill"] = {"ok": ok, "summary": rd_slot}
        except Exception as exc:  # noqa: BLE001
            log.exception("staff self-test persona drill failed for %s", role)
            out["checks"]["persona_drill"] = {"ok": False, "detail": str(exc)}

    out["ok"] = all(bool(c.get("ok")) for c in out["checks"].values())
    return out


async def run_desk_core_self_tests(
    caller: Principal,
    *,
    include_persona_drill: bool = False,
    runner: StaffRunner | None = None,
) -> dict[str, Any]:
    roles = desk_core_staff_roles()
    results = [
        await run_role_self_test(r, caller, runner=runner, include_persona_drill=include_persona_drill) for r in roles
    ]
    return {
        "roles": list(roles),
        "ok": all(r.get("ok") for r in results),
        "results": results,
    }
