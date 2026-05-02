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
