"""Static hygiene checks for `.github/workflows/*.yml`.

These tests enforce two CI invariants for every GitHub Actions workflow:

1. The workflow has a top-level ``concurrency:`` block, so concurrent triggers
   on the same ref do not pile up duplicate runs.
2. Every job in the workflow has ``timeout-minutes`` set, so a hung job cannot
   monopolize a self-hosted runner indefinitely.

Reusable-workflow caller jobs (``uses:`` without ``steps:``) are exempt from
the timeout requirement — the called workflow owns the timeout for its own
jobs and GitHub Actions does not honor ``timeout-minutes`` on a caller job.

Tracking: issue #429.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_WORKFLOWS_DIR = Path(__file__).parent.parent / ".github" / "workflows"


def _workflow_files() -> list[Path]:
    files = sorted(_WORKFLOWS_DIR.glob("*.yml"))
    assert files, f"No workflow files found under {_WORKFLOWS_DIR}"
    return files


def _load_workflow(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name}: top-level YAML must be a mapping"
    return data


def _is_reusable_caller(job_body: dict) -> bool:
    """A reusable-workflow caller has ``uses:`` and no ``steps:``.

    GitHub Actions does not honor ``timeout-minutes`` on caller jobs — the
    timeout lives on the called workflow's own jobs.
    """
    return "uses" in job_body and "steps" not in job_body


@pytest.mark.parametrize(
    "workflow_path",
    _workflow_files(),
    ids=lambda p: p.name,
)
def test_workflow_has_concurrency_block(workflow_path: Path) -> None:
    """Every workflow must declare a top-level ``concurrency:`` group.

    Without it, repeated triggers (e.g. rapid pushes, schedule overlap) can
    pile up duplicate runs and starve self-hosted runners.
    """
    data = _load_workflow(workflow_path)
    assert "concurrency" in data, (
        f"{workflow_path.name}: missing top-level `concurrency:` block. "
        f"Add `concurrency: {{ group: ${{{{ github.workflow }}}}-${{{{ github.ref }}}}, "
        f"cancel-in-progress: true }}` (use `cancel-in-progress: false` for "
        f"deploy/release/repair flows)."
    )
    block = data["concurrency"]
    assert isinstance(block, dict), f"{workflow_path.name}: `concurrency:` must be a mapping with `group:`."
    assert block.get("group"), f"{workflow_path.name}: `concurrency.group` must be a non-empty string."
    assert "cancel-in-progress" in block, (
        f"{workflow_path.name}: `concurrency.cancel-in-progress` must be set "
        f"(true for fast-forward CI, false for deploy/release/repair flows)."
    )


@pytest.mark.parametrize(
    "workflow_path",
    _workflow_files(),
    ids=lambda p: p.name,
)
def test_workflow_jobs_have_timeout_minutes(workflow_path: Path) -> None:
    """Every job (except reusable-workflow callers) must set ``timeout-minutes``.

    Sane defaults: lint/quality 10, tests 20, integration 30, deploy 15.
    Without an explicit timeout, GitHub Actions defaults to 360 minutes
    (6 hours), which can trap a self-hosted runner on a hung job.
    """
    data = _load_workflow(workflow_path)
    jobs = data.get("jobs") or {}
    assert jobs, f"{workflow_path.name}: workflow has no `jobs:` block"

    missing: list[str] = []
    for job_name, job_body in jobs.items():
        job_body = job_body or {}
        if _is_reusable_caller(job_body):
            continue
        timeout = job_body.get("timeout-minutes")
        if timeout is None:
            missing.append(job_name)
            continue
        assert isinstance(timeout, int) and timeout > 0, (
            f"{workflow_path.name}: job `{job_name}` has invalid "
            f"`timeout-minutes: {timeout!r}` — must be a positive integer."
        )

    assert not missing, (
        f"{workflow_path.name}: jobs missing `timeout-minutes`: {missing}. "
        f"Add `timeout-minutes:` under each job (lint/quality 10, tests 20, "
        f"integration 30, deploy 15)."
    )


def test_pr_autofix_is_not_a_second_automatic_ci_remediation_entrypoint() -> None:
    """Issue #596: automatic CI remediation must be owned by Control Tower."""
    autofix = _load_workflow(_WORKFLOWS_DIR / "Jules-PR-AutoFix.yml")
    triggers = autofix.get(True) or autofix.get("on") or {}

    assert "workflow_run" not in triggers, (
        "Jules-PR-AutoFix.yml must not listen directly to workflow_run; "
        "Control Tower is the single automatic CI remediation owner."
    )
    assert "workflow_call" in triggers, "Jules-PR-AutoFix.yml must remain callable by Control Tower."
    assert "workflow_dispatch" in triggers, "Jules-PR-AutoFix.yml must preserve manual maintainer dispatch."

    control_tower_text = (_WORKFLOWS_DIR / "Jules-Control-Tower.yml").read_text(encoding="utf-8")
    assert "uses: ./.github/workflows/Jules-PR-AutoFix.yml" in control_tower_text
    assert 'remediation_owner: "Jules Control Tower"' in control_tower_text


def test_pr_autofix_uses_bounded_rest_verification_not_tight_pr_checks_polling() -> None:
    """Issue #597: avoid tight GraphQL polling through `gh pr checks`."""
    text = (_WORKFLOWS_DIR / "Jules-PR-AutoFix.yml").read_text(encoding="utf-8")

    assert "gh pr checks" not in text
    assert "CI_POLL_INTERVAL" not in text
    assert "CI_VERIFY_ATTEMPTS" in text
    assert "actions/runs?branch=" in text
    assert "deferred" in text


def test_pr_autofix_preserves_branch_safety_and_status_contract() -> None:
    """Issues #596/#597: keep protected branch guard and explicit outcomes."""
    text = (_WORKFLOWS_DIR / "Jules-PR-AutoFix.yml").read_text(encoding="utf-8")

    assert '"$BRANCH" == "main"' in text
    assert '"$BRANCH" == "master"' in text
    assert "Cannot auto-fix protected branch" in text
    for status in ("passed", "failed", "deferred", "manual_required"):
        assert status in text


# Allowlists for PR-triggered workflows that do not cancel-in-progress or use a singleton group.

# PR-triggered workflows where cancel-in-progress: false is intentional.
# Must map workflow filename to a clear rationale.
FALSE_CANCEL_PR_ALLOWLIST: dict[str, str] = {}

# PR-triggered workflows that collapse all PRs into a single concurrency group.
# Must map workflow filename to a clear rationale why it is an intentional singleton.
SINGLETON_PR_ALLOWLIST: dict[str, str] = {
    "Agent-Redundant-PR-Closer.yml": (
        "Closes redundant agent PRs across the repository. It scans the entire pool of open PRs, "
        "so running it as a global singleton avoids race conditions, API rate limits, "
        "and duplicate comments."
    ),
    "Jules-Control-Tower.yml": (
        "The central orchestrator/dispatcher for automated remediation and triage. Running "
        "as a repository singleton avoids race conditions and duplicate dispatches."
    ),
}


def _is_pr_triggered(data: dict) -> bool:
    """Check if a workflow is triggered by pull_request or pull_request_target events."""
    triggers = data.get(True) or data.get("on")
    if not triggers:
        return False
    if isinstance(triggers, str):
        return triggers in ("pull_request", "pull_request_target")
    if isinstance(triggers, list):
        return any(t in ("pull_request", "pull_request_target") for t in triggers)
    if isinstance(triggers, dict):
        return "pull_request" in triggers or "pull_request_target" in triggers
    return False


@pytest.mark.parametrize(
    "workflow_path",
    _workflow_files(),
    ids=lambda p: p.name,
)
def test_pr_workflows_cancel_in_progress_policy(workflow_path: Path) -> None:
    """PR-triggered workflows must have cancel-in-progress: true unless allowlisted."""
    data = _load_workflow(workflow_path)
    if not _is_pr_triggered(data):
        pytest.skip(f"Workflow {workflow_path.name} is not PR-triggered.")

    concurrency = data.get("concurrency")
    assert concurrency is not None, f"{workflow_path.name}: PR-triggered workflow must have a concurrency block."

    cancel_in_progress = concurrency.get("cancel-in-progress")
    if workflow_path.name in FALSE_CANCEL_PR_ALLOWLIST:
        assert cancel_in_progress is False, (
            f"{workflow_path.name} is allowlisted to have cancel-in-progress: false, "
            f"but it is set to {cancel_in_progress}."
        )
    else:
        assert cancel_in_progress is True, (
            f"{workflow_path.name}: PR-triggered workflow must have cancel-in-progress: true. "
            f"If false is intentional, add it to FALSE_CANCEL_PR_ALLOWLIST with a clear rationale."
        )


@pytest.mark.parametrize(
    "workflow_path",
    _workflow_files(),
    ids=lambda p: p.name,
)
def test_pr_workflows_concurrency_group_uniqueness(workflow_path: Path) -> None:
    """PR-triggered workflows must not collapse all PRs into a single group unless allowlisted as a singleton."""
    data = _load_workflow(workflow_path)
    if not _is_pr_triggered(data):
        pytest.skip(f"Workflow {workflow_path.name} is not PR-triggered.")

    concurrency = data.get("concurrency")
    assert concurrency is not None, f"{workflow_path.name}: PR-triggered workflow must have a concurrency block."

    group = concurrency.get("group")
    assert isinstance(group, str), f"{workflow_path.name}: concurrency group must be a string."

    # A concurrency group collapses all PRs if it does not contain a variable that varies by PR or ref
    ref_vars = [
        "github.event.pull_request.number",
        "github.ref",
        "github.ref_name",
        "github.head_ref",
        "github.sha",
        "github.event.number",
        "inputs.branch",
    ]
    has_ref_var = any(var in group for var in ref_vars)

    if workflow_path.name in SINGLETON_PR_ALLOWLIST:
        assert not has_ref_var, (
            f"{workflow_path.name} is allowlisted as a singleton, but it has a dynamic group name "
            f"containing a ref variable: {group}"
        )
    else:
        assert has_ref_var, (
            f"{workflow_path.name}: PR-triggered workflow concurrency group '{group}' collapses all PRs. "
            f"Add a dynamic variable like '${{{{ github.ref }}}}' or '${{{{ github.event.pull_request.number }}}}' "
            f"to the concurrency group, or add the workflow to SINGLETON_PR_ALLOWLIST with a clear rationale."
        )
