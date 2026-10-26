"""YAML contract for the slim merge_group gate (issue #2891, epic #2886).

Asserts which jobs run on ``merge_group``, that required contexts are not
skippable there, and that jobs dropped from the queue still run on
``pull_request``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
_CI_STANDARD = _WORKFLOWS / "ci-standard.yml"

# Jobs that must execute on merge_group per workflow file (issue #2891).
MERGE_GROUP_JOB_ALLOWLIST: dict[str, frozenset[str]] = {
    "ci-standard.yml": frozenset(
        {
            "queue-entry-alive",
            "pick-runner",
            "changes",
            "ci-health-check",
            "lint",
            "tests",
            "tests-required",
            "quality-gate",
        }
    ),
    "frontend-tests.yml": frozenset(
        {
            "queue-entry-alive",
            "frontend-scope",
            "typecheck",
            "vitest",
            "frontend-required",
        }
    ),
    "ci-secrets.yml": frozenset({"queue-entry-alive", "detect-secrets"}),
    "docker-build.yml": frozenset({"queue-entry-alive"}),
    "ci-auto-deploy-regression.yml": frozenset({"queue-entry-alive"}),
    "anti-phantom-merge.yml": frozenset(),
}

# Heavy jobs that must not run on merge_group but must remain on pull_request.
MERGE_GROUP_EXCLUDED_JOBS: dict[str, frozenset[str]] = {
    "ci-standard.yml": frozenset({"security-scan"}),
    "frontend-tests.yml": frozenset(
        {"perf-budget", "playwright-e2e", "staff-console-e2e"}
    ),
    "ci-secrets.yml": frozenset({"gitleaks"}),
    "docker-build.yml": frozenset(
        {"decide-docker-scope", "docker-image", "docker-build-scan", "publish-sarif"}
    ),
    "ci-auto-deploy-regression.yml": frozenset({"auto-deploy-regression"}),
}

_REQUIRED_MERGE_GROUP_CONTEXT_JOBS: tuple[tuple[str, str], ...] = (
    ("ci-standard.yml", "quality-gate"),
    ("ci-standard.yml", "tests-required"),
)


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _triggers(data: dict) -> dict | str | list:
    return data.get(True) or data.get("on") or {}


def _has_merge_group_trigger(data: dict) -> bool:
    triggers = _triggers(data)
    if isinstance(triggers, dict):
        return "merge_group" in triggers
    if isinstance(triggers, list):
        return "merge_group" in triggers
    return False


def _merge_group_trigger_has_path_filter(data: dict) -> bool:
    triggers = _triggers(data)
    if not isinstance(triggers, dict):
        return False
    mg = triggers.get("merge_group")
    if mg is None:
        return False
    if isinstance(mg, dict):
        return bool(mg.get("paths") or mg.get("paths-ignore"))
    return False


def _job_if(job: dict) -> str:
    return str(job.get("if", "")).strip()


def _job_runs_on_merge_group(job: dict) -> bool:
    """True when the job is not explicitly excluded from merge_group."""
    cond = _job_if(job)
    if not cond:
        return True
    if re.search(r"github\.event_name\s*!=\s*['\"]merge_group['\"]", cond):
        return False
    if re.search(r"!=\s*['\"]merge_group['\"]", cond):
        return False
    # pull_request-only jobs (guard, label handlers) do not run in the queue.
    if "merge_group" not in cond and re.search(
        r"github\.event_name\s*==\s*['\"]pull_request['\"]", cond
    ):
        return False
    return True


def _job_runs_on_pull_request(job: dict) -> bool:
    cond = _job_if(job)
    if not cond:
        return True
    if re.search(r"github\.event_name\s*==\s*['\"]merge_group['\"]", cond):
        return False
    if re.search(r"==\s*['\"]merge_group['\"]", cond) and "!=" not in cond:
        return False
    return True


@pytest.mark.parametrize("filename", sorted(MERGE_GROUP_JOB_ALLOWLIST))
def test_merge_group_job_set_matches_allowlist(filename: str) -> None:
    path = _WORKFLOWS / filename
    data = _load(path)
    assert _has_merge_group_trigger(data), f"{filename} must keep a merge_group trigger when listed"
    jobs = data.get("jobs") or {}
    running = {job_id for job_id, job in jobs.items() if _job_runs_on_merge_group(job)}
    assert running == set(MERGE_GROUP_JOB_ALLOWLIST[filename]), (
        f"{filename}: merge_group job set mismatch.\n"
        f"  expected: {sorted(MERGE_GROUP_JOB_ALLOWLIST[filename])}\n"
        f"  actual:   {sorted(running)}"
    )


@pytest.mark.parametrize(
    ("filename", "job_id"),
    sorted(
        (fn, jid)
        for fn, jobs in sorted(MERGE_GROUP_EXCLUDED_JOBS.items())
        for jid in sorted(jobs)
    ),
)
def test_excluded_jobs_still_run_on_pull_request(filename: str, job_id: str) -> None:
    path = _WORKFLOWS / filename
    data = _load(path)
    triggers = _triggers(data)
    has_pr = isinstance(triggers, dict) and "pull_request" in triggers
    assert has_pr, f"{filename}: dropped merge_group jobs must still run on pull_request"
    job = (data.get("jobs") or {}).get(job_id)
    assert job is not None, f"{filename} missing job {job_id}"
    assert not _job_runs_on_merge_group(job), f"{job_id} must not run on merge_group"
    assert _job_runs_on_pull_request(job), f"{job_id} must still run on pull_request"


@pytest.mark.parametrize(("filename", "job_id"), _REQUIRED_MERGE_GROUP_CONTEXT_JOBS)
def test_required_context_jobs_always_run_on_merge_group(filename: str, job_id: str) -> None:
    path = _WORKFLOWS / filename
    job = (_load(path).get("jobs") or {})[job_id]
    assert _job_runs_on_merge_group(job), f"{job_id} must run on merge_group"
    cond = _job_if(job)
    assert "merge_group" not in cond or "!=" in cond, (
        f"{job_id} must not use a merge_group-skipping `if:` (got {cond!r})"
    )
    assert cond in ("", "always()"), (
        f"{job_id} must use `if: always()` so merge_group never skips the required context"
    )


def test_merge_group_triggers_have_no_path_filters() -> None:
    for path in sorted(_WORKFLOWS.glob("*.yml")):
        data = _load(path)
        if not _has_merge_group_trigger(data):
            continue
        assert not _merge_group_trigger_has_path_filter(data), (
            f"{path.name}: merge_group trigger must not use paths/path-ignore filters"
        )


def test_quality_gate_needs_cover_merge_group_allowlist() -> None:
    jobs = _load(_CI_STANDARD)["jobs"]
    gate = jobs["quality-gate"]
    needs = set(gate["needs"])
    allowlisted = MERGE_GROUP_JOB_ALLOWLIST["ci-standard.yml"] - {
        "quality-gate",
        "queue-entry-alive",
        "pick-runner",
        "tests",
    }
    assert allowlisted <= needs, (
        f"quality-gate needs must include every merge_group job except itself; "
        f"missing {sorted(allowlisted - needs)}"
    )
    assert "security-scan" not in needs or "security-scan" not in allowlisted


def test_quality_gate_fails_closed_on_merge_group_in_script() -> None:
    jobs = _load(_CI_STANDARD)["jobs"]
    step = next(
        s
        for s in jobs["quality-gate"]["steps"]
        if s.get("name") == "Require every gated job to succeed"
    )
    run = step["run"]
    assert "merge_group" in run or "merge-group" in run or "merge_queue_lane" in run, (
        "quality-gate must fail closed on merge_group skipped/cancelled/missing inputs"
    )


def test_tests_required_fails_closed_on_merge_group_cancel() -> None:
    jobs = _load(_CI_STANDARD)["jobs"]
    step = next(s for s in jobs["tests-required"]["steps"] if "Confirm" in s.get("name", ""))
    run = step["run"]
    assert "merge_group" in run, (
        "tests-required must treat cancelled/skipped matrix as failure on merge_group"
    )
