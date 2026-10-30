"""Tests for issue #3612: main `Frontend Tests` red from a stale self-hosted workspace.

`playwright-e2e` and `staff-console-e2e` run on the self-hosted
`d-sorg-fleet-public-heavy` pool (see tests/test_public_repo_heavy_job_runner_split.py).
A prior merge-queue speculative-merge checkout left those runners' persistent
workspace mid sparse-checkout-transition; the next push-triggered job reused
that dirty workspace and `actions/checkout`'s own clean/reset sequence could
not fully reconcile it, so `git checkout -B main` partially failed and
`npm ci` broke on a missing `package-lock.json` (run 41021346111 and 9+ prior
runs). Each job must wipe `$GITHUB_WORKSPACE` before `actions/checkout` runs,
so every job starts from a guaranteed-empty directory regardless of what a
prior job left behind.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "frontend-tests.yml"
_HEAVY_JOB_IDS = ("playwright-e2e", "staff-console-e2e")


def _jobs() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))["jobs"]


def _first_checkout_index(steps: list[dict[str, Any]]) -> int:
    for index, step in enumerate(steps):
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            return index
    raise AssertionError("no actions/checkout step found")


def test_heavy_e2e_jobs_reset_workspace_before_checkout() -> None:
    jobs = _jobs()
    for job_id in _HEAVY_JOB_IDS:
        steps = jobs[job_id]["steps"]
        checkout_index = _first_checkout_index(steps)
        assert checkout_index > 0, f"{job_id}: no step precedes actions/checkout"
        reset_step = steps[checkout_index - 1]
        assert reset_step.get("name") == "Reset workspace (stale self-hosted checkout guard)", (
            f"{job_id}: step immediately before actions/checkout is not the workspace reset"
        )
        run = reset_step.get("run", "")
        assert "rm -rf" in run and "GITHUB_WORKSPACE" in run, f"{job_id}: reset step does not wipe $GITHUB_WORKSPACE"


def test_non_heavy_jobs_are_unaffected() -> None:
    """Only the two self-hosted-heavy jobs need the guard; don't widen scope."""
    jobs = _jobs()
    for job_id in ("typecheck", "vitest", "perf-budget"):
        steps = jobs[job_id]["steps"]
        names = {step.get("name") for step in steps}
        assert "Reset workspace (stale self-hosted checkout guard)" not in names
