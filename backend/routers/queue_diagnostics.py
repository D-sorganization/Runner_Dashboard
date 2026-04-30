"""Queue diagnostic routes for analyzing workflow run delays."""

import logging

from cache_utils import cache_get, cache_set
from fastapi import APIRouter

log = logging.getLogger("dashboard.queue_diagnostics")
router = APIRouter()


@router.get("/api/queue/diagnose")
async def diagnose_queue() -> dict:
    """Explain why queued jobs are waiting (stub implementation)."""
    cached = cache_get("diagnose", 120.0)
    if cached is not None:
        return cached
    result = {
        "runner_pool": {"total": 0, "online": 0, "busy": 0, "idle": 0, "offline": 0},
        "queued_runs_found": 0,
        "jobs_sampled": 0,
        "waiting_for_fleet": 0,
        "waiting_for_generic_self_hosted": 0,
        "waiting_for_self_hosted": 0,
        "waiting_for_github_hosted": 0,
        "runner_groups": [],
        "runner_groups_restricted": False,
        "pick_runner_misconfig": [],
        "label_breakdown": {},
        "bottleneck": "Diagnosis not yet initialized",
        "sampled_jobs": [],
    }
    cache_set("diagnose", result)
    return result
