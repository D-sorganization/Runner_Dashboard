"""Runner control and status routes."""

from __future__ import annotations

import logging
from pathlib import Path

from cache_utils import cache_get, cache_set
from dashboard_config import ORG, RUNNER_BASE_DIR
from fastapi import APIRouter, Depends, HTTPException, Request
from gh_utils import gh_api_admin
from identity import Principal, require_scope
from proxy_utils import proxy_to_hub, should_proxy_fleet_to_hub
from system_utils import run_cmd

log = logging.getLogger("dashboard.runners")
router = APIRouter(tags=["runners"])


def runner_svc_path(runner_num: int) -> Path:
    """Return the path to a runner's svc.sh script."""
    return RUNNER_BASE_DIR / f"runner-{runner_num}" / "svc.sh"


async def run_runner_svc(runner_num: int, action: str, timeout: int = 30) -> tuple[int, str, str]:
    """Execute ./svc.sh <action> for a runner."""
    svc_path = runner_svc_path(runner_num)
    if not svc_path.exists():
        return 1, "", f"Service script not found: {svc_path}"
    # Use sudo if required, or run directly if permissions allow
    cmd = ["sudo", "-n", str(svc_path), action]
    return await run_cmd(cmd, timeout=timeout)


def runner_num_from_id(runner_id: int, runners: list[dict]) -> int | None:
    """Extract local 1-based runner index from a GitHub runner dict's name."""
    for r in runners:
        if r.get("id") == runner_id:
            name = r.get("name", "")
            # Expecting names like "d-sorg-fleet-runner-1"
            if "runner-" in name:
                try:
                    return int(name.split("runner-")[-1])
                except (ValueError, IndexError):
                    pass
    return None


def _runner_sort_key(runner: dict) -> tuple[str, int, str]:
    """Sort key for runners: status (online first), then local index, then name."""
    status_rank = "0" if runner.get("status") == "online" else "1"
    name = runner.get("name", "")
    try:
        num = int(name.split("-")[-1]) if "-" in name else 0
    except (ValueError, IndexError):
        num = 0
    return (status_rank, num, name)


def _is_matlab_runner(runner: dict) -> bool:
    """Return True if the runner appears to have MATLAB installed (by labels)."""
    labels = [lbl.get("name", "").lower() for lbl in runner.get("labels", []) if isinstance(lbl, dict)]
    return "matlab" in labels or "windows-matlab" in labels


def _matlab_runner_summary(runner: dict) -> dict:
    """Extracted summary for MATLAB runners."""
    return {
        "id": runner.get("id"),
        "name": runner.get("name"),
        "status": runner.get("status"),
        "busy": runner.get("busy"),
        "labels": [lbl.get("name") for lbl in runner.get("labels", []) if isinstance(lbl, dict)],
    }


@router.get("/api/runners")
async def get_runners(request: Request):
    """Get all org runners with their status."""
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    cached = cache_get("runners", 60.0)
    if cached is not None:
        cached["runners"] = sorted(cached.get("runners", []), key=_runner_sort_key)
        return cached

    try:
        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        data["runners"] = sorted(data.get("runners", []), key=_runner_sort_key)
        cache_set("runners", data)
        return data
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}") from exc


@router.get("/api/runners/matlab")
async def get_matlab_runner_health(request: Request) -> dict:
    """Surface Windows MATLAB runner health for the dashboard."""
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    cached = cache_get("matlab_runner_health", 45.0)
    if cached is not None:
        return cached

    try:
        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        all_runners = data.get("runners", []) or []
    except Exception:
        all_runners = []

    matlab = [r for r in all_runners if _is_matlab_runner(r)]
    summaries = [_matlab_runner_summary(r) for r in matlab]

    res = {
        "runners": summaries,
        "total": len(summaries),
        "online": sum(1 for r in summaries if r["status"] == "online"),
        "busy": sum(1 for r in summaries if r["busy"]),
        "offline": sum(1 for r in summaries if r["status"] != "online"),
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
    }
    cache_set("matlab_runner_health", res)
    return res


@router.post("/api/runners/{runner_id}/stop")
async def stop_runner(
    request: Request,
    runner_id: int,
    principal: Principal = Depends(require_scope("runners.control")),  # noqa: B008
):
    """Stop a specific runner's service."""
    data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
    runners = data.get("runners", [])
    num = runner_num_from_id(runner_id, runners)

    if num is None:
        raise HTTPException(status_code=404, detail=f"Runner ID {runner_id} not found locally")

    code, stdout, stderr = await run_runner_svc(num, "stop")
    if code != 0:
        raise HTTPException(status_code=500, detail=f"Failed to stop runner {num}: {stderr}")

    return {"status": "stopped", "runner": num, "output": stdout.strip()}


@router.post("/api/runners/{runner_id}/start")
async def start_runner(
    request: Request,
    runner_id: int,
    principal: Principal = Depends(require_scope("runners.control")),  # noqa: B008
):
    """Start a specific runner's service."""
    data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
    runners = data.get("runners", [])
    num = runner_num_from_id(runner_id, runners)

    if num is None:
        raise HTTPException(status_code=404, detail=f"Runner ID {runner_id} not found locally")

    code, stdout, stderr = await run_runner_svc(num, "start")
    if code != 0:
        raise HTTPException(status_code=500, detail=f"Failed to start runner {num}: {stderr}")

    return {"status": "started", "runner": num, "output": stdout.strip()}
