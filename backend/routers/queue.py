"""Queue management routes.

Covers:
  - GET  /api/queue              – queued and in-progress workflow runs (org-wide sample)
  - POST /api/runs/{repo}/cancel/{run_id}      – cancel single workflow run
  - POST /api/runs/{repo}/rerun/{run_id}       – re-run failed jobs in workflow
  - POST /api/queue/cancel-workflow             – cancel all queued runs of a workflow
  - GET  /api/queue/diagnose                    – explain why queued jobs are waiting
"""

from __future__ import annotations

import asyncio
import json
import logging

from cache_utils import cache_delete, cache_get, cache_set
from dashboard_config import ORG
from fastapi import APIRouter, Depends, HTTPException, Request
from gh_utils import gh_api_admin
from identity import Principal, require_scope
from proxy_utils import proxy_to_hub, should_proxy_fleet_to_hub
from system_utils import run_cmd

log = logging.getLogger("dashboard.queue")
router = APIRouter(tags=["queue"])


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _empty_queue_result() -> dict:
    """Return the standard empty queue payload."""
    return {
        "queued": [],
        "in_progress": [],
        "total": 0,
        "queued_count": 0,
        "in_progress_count": 0,
    }


async def _get_recent_org_repos(limit: int = 30) -> list[dict]:
    """Fetch recently updated organization repositories."""
    code, stdout, _ = await run_cmd(
        [
            "gh",
            "api",
            f"/orgs/{ORG}/repos?per_page={limit}&sort=updated&direction=desc",
        ],
        timeout=20,
    )
    if code != 0:
        return []
    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return []


async def _fetch_repo_runs(
    repo_name: str,
    *,
    per_page: int = 10,
    status: str | None = None,
) -> list[dict]:
    """Fetch workflow runs for one repository and annotate repository name."""
    status_part = f"&status={status}" if status else ""
    rc, out, _ = await run_cmd(
        [
            "gh",
            "api",
            f"/repos/{ORG}/{repo_name}/actions/runs?per_page={per_page}{status_part}",
        ],
        timeout=15,
    )
    if rc != 0:
        return []
    try:
        runs = json.loads(out).get("workflow_runs", [])
    except (json.JSONDecodeError, ValueError):
        return []
    for run in runs:
        if "repository" not in run or not run["repository"]:
            run["repository"] = {"name": repo_name}
    return runs


async def _queue_impl() -> dict:
    """Core queue aggregation, callable from the HTTP endpoint and internally."""
    cached = cache_get("queue", 120.0)
    if cached is not None:
        return cached

    repos = await _get_recent_org_repos(limit=20)
    if not repos:
        return _empty_queue_result()

    async def fetch_active_runs(repo_name: str) -> list[dict]:
        results: list[dict] = []
        for status in ("queued", "in_progress"):
            results.extend(await _fetch_repo_runs(repo_name, per_page=10, status=status))
        return results

    sample = repos[:15]
    all_runs_nested = await asyncio.gather(*[fetch_active_runs(r["name"]) for r in sample])
    all_runs: list[dict] = [run for sublist in all_runs_nested for run in sublist]

    queued = sorted(
        [r for r in all_runs if r.get("status") == "queued"],
        key=lambda r: r.get("created_at", ""),
    )
    in_progress = sorted(
        [r for r in all_runs if r.get("status") == "in_progress"],
        key=lambda r: r.get("run_started_at") or r.get("created_at", ""),
    )

    result = {
        "queued": queued,
        "in_progress": in_progress,
        "total": len(queued) + len(in_progress),
        "queued_count": len(queued),
        "in_progress_count": len(in_progress),
    }
    cache_set("queue", result)
    return result


# ─── Queue Routes ─────────────────────────────────────────────────────────────


@router.get("/api/queue")
async def get_queue(request: Request) -> dict:
    """Get queued and in-progress workflow runs across the org.

    GitHub has no org-level queue endpoint; we query the 15 most recently
    updated repos concurrently for both statuses and aggregate the results.
    """
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    return await _queue_impl()


@router.post("/api/runs/{repo}/cancel/{run_id}")
async def cancel_run(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("workflows.control")),
    repo: str,
    run_id: int,  # noqa: B008
) -> dict:
    """Cancel a single queued or in-progress workflow run."""
    code, _, stderr = await run_cmd(
        [
            "gh",
            "api",
            "-X",
            "POST",
            f"/repos/{ORG}/{repo}/actions/runs/{run_id}/cancel",
        ],
        timeout=15,
    )
    if code != 0:
        raise HTTPException(status_code=502, detail=f"Cancel failed: {stderr}")
    # Invalidate stale queue/runs caches so the next poll reflects the cancel.
    cache_delete("queue")
    cache_delete("diagnose")
    return {"cancelled": True, "run_id": run_id, "repo": repo}


@router.post("/api/runs/{repo}/rerun/{run_id}")
async def rerun_failed(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("workflows.control")),
    repo: str,
    run_id: int,  # noqa: B008
) -> dict:
    """Re-run failed jobs in a workflow run."""
    code, _, stderr = await run_cmd(
        [
            "gh",
            "api",
            "-X",
            "POST",
            f"/repos/{ORG}/{repo}/actions/runs/{run_id}/rerun-failed-jobs",
        ],
        timeout=15,
    )
    if code != 0:
        raise HTTPException(status_code=502, detail=f"Rerun failed: {stderr}")
    cache_delete("queue")
    return {"rerun": True, "run_id": run_id, "repo": repo}


@router.post("/api/queue/cancel-workflow")
async def cancel_workflow_runs(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("workflows.control")),  # noqa: B008
) -> dict:
    """Cancel all queued runs of a specific workflow across the org.

    Body: {"workflow_name": "ci-standard", "repo": "MyRepo"}  (repo optional)
    Useful for deprioritising a noisy workflow to free runners for
    higher-priority work.
    """
    body = await request.json()
    workflow_name: str = body.get("workflow_name", "")
    target_repo: str | None = body.get("repo")

    if not workflow_name:
        raise HTTPException(status_code=400, detail="workflow_name required")

    # Fetch current queue
    queue_data = await _queue_impl()
    runs_to_cancel = [
        r
        for r in queue_data["queued"]
        if r.get("name") == workflow_name
        and (target_repo is None or (r.get("repository") or {}).get("name") == target_repo)  # noqa: E501
    ]

    cancelled: list[dict] = []
    errors: list[str] = []
    for run in runs_to_cancel:
        repo = (run.get("repository") or {}).get("name", "")
        run_id = run["id"]
        if not repo:
            continue
        code, _, stderr = await run_cmd(
            [
                "gh",
                "api",
                "-X",
                "POST",
                f"/repos/{ORG}/{repo}/actions/runs/{run_id}/cancel",
            ],
            timeout=15,
        )
        if code == 0:
            cancelled.append({"repo": repo, "run_id": run_id})
        else:
            errors.append(f"{repo}#{run_id}: {stderr.strip()}")

    if cancelled:
        cache_delete("queue")
        cache_delete("diagnose")

    return {
        "cancelled_count": len(cancelled),
        "cancelled": cancelled,
        "errors": errors,
    }


@router.get("/api/queue/diagnose")
async def diagnose_queue() -> dict:
    """Explain why queued jobs are waiting.

    Samples queued workflow runs, fetches their jobs, and reports which runner
    labels the waiting jobs need — self-hosted fleet, ubuntu-latest, or other.
    Cross-references against the live runner pool to identify the bottleneck.
    """
    cached = cache_get("diagnose", 120.0)
    if cached is not None:
        return cached

    # Runner pool status
    try:
        runner_data = cache_get("runners", 25.0)
        if runner_data is None:
            runner_data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
            cache_set("runners", runner_data)
        runners = runner_data.get("runners", [])
    except Exception:  # noqa: BLE001
        runners = []

    online = [r for r in runners if r["status"] == "online"]
    busy = [r for r in runners if r.get("busy")]
    idle = [r for r in online if not r.get("busy")]
    online_runner_names = {r.get("name", "") for r in online}

    # Collect queued runs across repos
    code, stdout, _ = await run_cmd(
        ["gh", "api", f"/orgs/{ORG}/repos?per_page=20&sort=updated&direction=desc"],
        timeout=20,
    )
    if code != 0:
        return {"error": "Cannot reach GitHub API — check GH_TOKEN in service"}

    try:
        repos = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return {"error": "Invalid response from GitHub API"}

    queued_runs: list[dict] = []
    for repo in repos[:15]:
        rc, out, _ = await run_cmd(
            [
                "gh",
                "api",
                f"/repos/{ORG}/{repo['name']}/actions/runs?status=queued&per_page=5",
            ],
            timeout=10,
        )
        if rc != 0:
            continue
        try:
            for run in json.loads(out).get("workflow_runs", []):
                run["_repo"] = repo["name"]
                queued_runs.append(run)
        except (json.JSONDecodeError, ValueError):
            continue

    # For each sampled run fetch its jobs to see what runner labels are needed
    async def get_run_jobs(run: dict) -> list[dict]:
        rc, out, _ = await run_cmd(
            [
                "gh",
                "api",
                f"/repos/{ORG}/{run['_repo']}/actions/runs/{run['id']}/jobs?per_page=30",
            ],
            timeout=10,
        )
        if rc != 0:
            return []
        try:
            return json.loads(out).get("jobs", [])
        except (json.JSONDecodeError, ValueError):
            return []

    sample = queued_runs[:20]
    all_jobs_nested = await asyncio.gather(*[get_run_jobs(r) for r in sample])

    # Labels that GitHub automatically applies to every self-hosted runner
    GENERIC_SELF_HOSTED = {
        "self-hosted",
        "linux",
        "Linux",
        "x64",
        "X64",
        "arm64",
        "ARM64",
        "windows",
        "Windows",
        "macOS",
    }
    GITHUB_HOSTED = {
        "ubuntu-latest",
        "ubuntu-22.04",
        "ubuntu-20.04",
        "ubuntu-24.04",
        "windows-latest",
        "macos-latest",
        "macos-14",
        "macos-13",
    }

    label_counts: dict[str, int] = {}
    waiting_for_fleet = 0
    waiting_for_generic_sh = 0
    waiting_for_github_hosted = 0
    sampled_jobs: list[dict] = []

    for run, jobs in zip(sample, all_jobs_nested, strict=False):
        for job in jobs:
            if job.get("status") != "queued":
                continue
            labels: list[str] = job.get("labels", [])
            for lbl in labels:
                label_counts[lbl] = label_counts.get(lbl, 0) + 1

            is_fleet = any(lbl.startswith("d-sorg") for lbl in labels)
            is_generic_sh = not is_fleet and any(lbl in GENERIC_SELF_HOSTED for lbl in labels)
            is_github = any(lbl in GITHUB_HOSTED for lbl in labels)

            if is_fleet:
                waiting_for_fleet += 1
            elif is_generic_sh:
                waiting_for_generic_sh += 1
            elif is_github:
                waiting_for_github_hosted += 1

            if is_fleet:
                target = "self-hosted (d-sorg-fleet)"
            elif is_generic_sh:
                target = "self-hosted (generic)"
            elif is_github:
                target = "github-hosted"
            else:
                target = "unknown"

            sampled_jobs.append(
                {
                    "repo": run["_repo"],
                    "run_id": run["id"],
                    "workflow": run.get("name"),
                    "job": job.get("name"),
                    "labels": labels,
                    "target": target,
                    "created_at": job.get("created_at"),
                }
            )

    waiting_for_self_hosted = waiting_for_fleet + waiting_for_generic_sh

    # Deep runner group check: fetch runners per group and allowed repos per
    # restricted group so we can pinpoint exactly which group the idle runners
    # belong to and which repos they can't see.
    runner_groups_info: list[dict] = []
    runner_groups_restricted = False
    runners_by_group: dict[int, list[str]] = {}  # group_id -> runner names

    async def fetch_group_runners(gid: int) -> list[str]:
        try:
            d = await gh_api_admin(f"/orgs/{ORG}/actions/runner-groups/{gid}/runners?per_page=100")
            return [r.get("name", "") for r in d.get("runners", [])]
        except Exception:  # noqa: BLE001
            return []

    async def fetch_group_repos(gid: int) -> list[str]:
        try:
            d = await gh_api_admin(f"/orgs/{ORG}/actions/runner-groups/{gid}/repositories?per_page=100")
            return [r.get("name", "") for r in d.get("repositories", [])]
        except Exception:  # noqa: BLE001
            return []

    try:
        rg_data = await gh_api_admin(f"/orgs/{ORG}/actions/runner-groups")
        raw_groups = rg_data.get("runner_groups", [])

        # Fetch runners for every group concurrently
        group_runner_lists = await asyncio.gather(*[fetch_group_runners(g["id"]) for g in raw_groups])
        for grp, grp_runners in zip(raw_groups, group_runner_lists, strict=False):
            runners_by_group[grp["id"]] = grp_runners

        # Fetch allowed repos for restricted groups
        restricted_groups = [g for g in raw_groups if g.get("visibility") != "all"]
        group_repo_lists = await asyncio.gather(*[fetch_group_repos(g["id"]) for g in restricted_groups])
        allowed_repos_by_group: dict[int, list[str]] = {
            g["id"]: repos for g, repos in zip(restricted_groups, group_repo_lists, strict=False)
        }

        # Collect repos with waiting jobs
        waiting_repos = {r["_repo"] for r in sample}

        for grp in raw_groups:
            gid = grp["id"]
            restricted = grp.get("visibility") != "all"
            grp_runners = runners_by_group.get(gid, [])
            allowed_repos = allowed_repos_by_group.get(gid, []) if restricted else []

            # Which waiting repos are blocked by this group's restrictions?
            blocked = [r for r in waiting_repos if r not in allowed_repos] if restricted else []

            runner_groups_info.append(
                {
                    "id": gid,
                    "name": grp.get("name"),
                    "visibility": grp.get("visibility"),
                    "restricted": restricted,
                    # True = enterprise-owned group
                    "inherited": grp.get("inherited", False),
                    "allows_public_repos": grp.get("allows_public_repositories", False),
                    "runner_count": len(grp_runners),
                    "runner_names": grp_runners[:8],  # cap for display
                    "allowed_repos": allowed_repos[:20] if restricted else [],
                    "blocked_waiting_repos": blocked,
                }
            )

            # Flag restriction if any group containing our idle runners is restricted
            # and is blocking at least one waiting repo
            if restricted and blocked and any(r in online_runner_names for r in grp_runners):
                runner_groups_restricted = True

    except Exception:  # noqa: BLE001
        pass  # Non-fatal

    # Detect pick-runner jobs that are themselves waiting on self-hosted
    # (misconfiguration: pick-runner should run on ubuntu-latest, not self-hosted)
    pick_runner_misconfig = [
        j
        for j in sampled_jobs
        if (j.get("job") or "").lower() in ("pick-runner", "pick runner", "select runner")  # noqa: E501
        and "self-hosted" in j.get("target", "")
    ]

    # Determine bottleneck
    if pick_runner_misconfig:
        repos_affected = sorted({j["repo"] for j in pick_runner_misconfig})
        bottleneck = (
            f"MISCONFIGURATION: {len(pick_runner_misconfig)} 'pick-runner' dispatcher "
            f"job(s) are themselves targeting 'self-hosted' in: "
            f"{', '.join(repos_affected)}. "
            "The pick-runner job must use 'runs-on: ubuntu-latest' (not 'self-hosted') — "  # noqa: E501
            "it is the dispatcher that decides where to send work. "
            "Update those workflow files to fix 'runs-on: ubuntu-latest' on the pick-runner job."  # noqa: E501
        )
    elif waiting_for_fleet > 0 and not idle:
        bottleneck = (
            f"All {len(busy)} d-sorg-fleet runner(s) are busy. "
            "Jobs will run as runners finish. Bring more machines online to increase throughput."  # noqa: E501
        )
    elif waiting_for_fleet > 0 and idle:
        bottleneck = (
            f"{len(idle)} idle fleet runner(s) exist but {waiting_for_fleet} fleet job(s) are "  # noqa: E501
            "still queued — possible label mismatch. Verify the runner labels include 'd-sorg-fleet'."  # noqa: E501
        )
    elif waiting_for_generic_sh > 0 and idle:
        if runner_groups_restricted:
            blocked_info = [
                f"'{g['name']}' (runners: {', '.join(g['runner_names'][:3])}{'…' if len(g['runner_names']) > 3 else ''}) "  # noqa: E501
                f"blocks: {', '.join(g['blocked_waiting_repos'][:5])}"
                for g in runner_groups_info
                if g["restricted"] and g["blocked_waiting_repos"]
            ]
            bottleneck = (
                f"RUNNER GROUP ACCESS RESTRICTION: {waiting_for_generic_sh} job(s) cannot "  # noqa: E501
                f"reach {len(idle)} idle runner(s). "
                + (" | ".join(blocked_info) + ". " if blocked_info else "")
                + "FIX: GitHub org Settings → Actions → Runner Groups → "
                "select the restricted group → set Repository access to 'All repositories'."  # noqa: E501
            )
        else:
            bottleneck = (
                f"{waiting_for_generic_sh} job(s) target the generic 'self-hosted' label "  # noqa: E501
                f"with {len(idle)} idle runner(s). "
                "Runners will pick these up — but check if any are pick-runner "  # noqa: E501
                "dispatcher jobs (they should use runs-on: ubuntu-latest, not "
                "self-hosted, to avoid wasting a runner slot on routing logic)."
            )
    elif waiting_for_generic_sh > 0 and not idle:
        bottleneck = (
            f"All {len(busy)} fleet runner(s) are busy and {waiting_for_generic_sh} job(s) "  # noqa: E501
            "target generic 'self-hosted'. Jobs will run as runners free up."
        )
    elif waiting_for_github_hosted > 0:
        bottleneck = (
            f"{waiting_for_github_hosted} job(s) are waiting for GitHub-hosted runners "
            "(ubuntu-latest). This is GitHub's queue — no local action possible. "
            "This may mean pick-runner routed them to the cloud because all fleet "
            "runners were busy when the dispatcher ran."
        )
    elif not sampled_jobs:
        bottleneck = "Could not sample job details — runs may have just started or GitHub API rate limit may be close."
    else:
        bottleneck = "Unknown — job labels did not match known runner targets."

    result = {
        "runner_pool": {
            "total": len(runners),
            "online": len(online),
            "busy": len(busy),
            "idle": len(idle),
            "offline": len(runners) - len(online),
        },
        "queued_runs_found": len(queued_runs),
        "jobs_sampled": len(sampled_jobs),
        "waiting_for_fleet": waiting_for_fleet,
        "waiting_for_generic_self_hosted": waiting_for_generic_sh,
        "waiting_for_self_hosted": waiting_for_self_hosted,
        "waiting_for_github_hosted": waiting_for_github_hosted,
        "runner_groups": runner_groups_info,
        "runner_groups_restricted": runner_groups_restricted,
        "pick_runner_misconfig": pick_runner_misconfig,
        "label_breakdown": label_counts,
        "bottleneck": bottleneck,
        "sampled_jobs": sampled_jobs[:15],
    }
    cache_set("diagnose", result)
    return result
