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

import json
from pathlib import Path

import pytest
import yaml

_WORKFLOWS_DIR = Path(__file__).parent.parent / ".github" / "workflows"
_POLICY_PATH = Path(__file__).parent.parent / "config" / "workflow_concurrency_policy.json"
_PR_TRIGGER_KEYS = ("pull_request", "pull_request_target")
_PR_GROUP_TOKENS = (
    "github.ref",
    "github.head_ref",
    "github.event.pull_request.number",
    "github.event.pull_request.head.sha",
    "github.event.number",
)

_CANCEL_FALSE_ALLOWLIST: dict[str, str] = {
    "dependabot-auto-merge.yml": "Arms auto-merge for one PR; cancelling mid-run can lose the policy note.",
    "lockfile-upgrade.yml": "Creates or updates a dependency PR; cancelling mid-PR write is noisy.",
    "release.yml": "Publishes release artifacts and tags; never interrupt a release mid-stream.",
    "verify-tag.yml": "Validates a pushed tag; tag verification should be immutable once started.",
}

_SINGLETON_GROUP_ALLOWLIST: dict[str, str] = {
    "Agent-Fleet-Dashboard.yml": "Scheduled singleton refresh; a newer refresh supersedes older work.",
    "Agent-Lease-Reaper.yml": "Scheduled singleton cleanup; overlapping runs should collapse.",
    "agent-panel-review.yml": "One panel review worker should own the review queue at a time.",
    "Agent-Redundant-PR-Closer.yml": "Singleton closer avoids competing PR-close decisions.",
    "ci-nightly.yml": "Nightly singleton; only one nightly run should occupy the fleet.",
    "issue-taxonomy-backfill.yml": "Backfill is an explicit singleton maintenance workflow.",
    "Jules-Control-Tower.yml": "Control Tower is the single automatic remediation coordinator.",
    "taxonomy-rollout.yml": "Taxonomy rollout is a singleton governance workflow.",
    "util-queued-job-reaper.yml": "Queue reaper is a singleton maintenance workflow with a max-cancel cap.",
}


def _workflow_files() -> list[Path]:
    files = sorted(_WORKFLOWS_DIR.glob("*.yml"))
    assert files, f"No workflow files found under {_WORKFLOWS_DIR}"
    return files


def _load_workflow(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name}: top-level YAML must be a mapping"
    return data


def _triggers(data: dict) -> dict | str | list:
    return data.get(True) or data.get("on") or {}


def _has_trigger(data: dict, name: str) -> bool:
    triggers = _triggers(data)
    if isinstance(triggers, str):
        return triggers == name
    if isinstance(triggers, list):
        return name in triggers
    if isinstance(triggers, dict):
        return name in triggers
    return False


def _workflow_triggers(path: Path) -> dict:
    data = _load_workflow(path)
    triggers = data.get("on")
    if triggers is None and True in data:
        triggers = data[True]
    assert isinstance(triggers, dict), f"{path.name}: workflow `on:` block must be a mapping"
    return triggers


def _load_concurrency_policy() -> dict[str, dict[str, str]]:
    data = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "workflow concurrency policy must be a JSON object"
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


def test_cancel_false_allowlist_is_documented_and_current() -> None:
    """Any false cancel exception must be explicit and carry a rationale."""
    false_cancel = []
    for workflow_path in _workflow_files():
        block = _load_workflow(workflow_path)["concurrency"]
        if block.get("cancel-in-progress") is False:
            false_cancel.append(workflow_path.name)

    assert sorted(false_cancel) == sorted(_CANCEL_FALSE_ALLOWLIST), (
        "Workflows with cancel-in-progress: false must be documented in "
        "_CANCEL_FALSE_ALLOWLIST with a release/deploy/PR-write rationale."
    )
    for workflow, reason in _CANCEL_FALSE_ALLOWLIST.items():
        assert reason and len(reason) >= 20, f"{workflow}: allowlist rationale is too thin"


@pytest.mark.parametrize(
    "workflow_path",
    _workflow_files(),
    ids=lambda p: p.name,
)
def test_pr_triggered_workflows_cancel_superseded_runs(workflow_path: Path) -> None:
    """PR workflows must cancel superseded runs unless explicitly allowlisted."""
    data = _load_workflow(workflow_path)
    if not (_has_trigger(data, "pull_request") or _has_trigger(data, "pull_request_target")):
        return
    if workflow_path.name in _CANCEL_FALSE_ALLOWLIST:
        return

    block = data["concurrency"]
    assert block.get("cancel-in-progress") is True, (
        f"{workflow_path.name}: PR-triggered workflows must cancel superseded "
        "runs unless they are documented in _CANCEL_FALSE_ALLOWLIST."
    )


@pytest.mark.parametrize(
    "workflow_path",
    _workflow_files(),
    ids=lambda p: p.name,
)
def test_pr_concurrency_groups_do_not_collapse_all_prs(workflow_path: Path) -> None:
    """PR workflow groups should distinguish PR/ref unless intentionally singleton."""
    data = _load_workflow(workflow_path)
    if not (_has_trigger(data, "pull_request") or _has_trigger(data, "pull_request_target")):
        return
    if workflow_path.name in _SINGLETON_GROUP_ALLOWLIST:
        return

    group = str(data["concurrency"].get("group") or "")
    assert "github.ref" in group or "pull_request.number" in group or "github.head_ref" in group, (
        f"{workflow_path.name}: PR-triggered concurrency group must include "
        "github.ref, github.head_ref, or github.event.pull_request.number "
        "unless documented as a singleton."
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


def test_workflow_concurrency_policy_references_real_workflows() -> None:
    """Issue #689: exception allowlists must stay explicit and maintainable."""
    policy = _load_concurrency_policy()
    workflow_names = {path.name for path in _workflow_files()}

    for policy_name, entries in policy.items():
        assert isinstance(entries, dict), f"{policy_name} must map workflow filenames to rationale strings."
        for workflow_name, rationale in entries.items():
            assert workflow_name in workflow_names, f"{policy_name}: unknown workflow `{workflow_name}` in policy file."
            assert isinstance(rationale, str) and rationale.strip(), (
                f"{policy_name}: `{workflow_name}` must have a non-empty rationale."
            )


def test_pr_workflows_use_cancel_in_progress_true_or_documented_exception() -> None:
    """Issue #689: PR-triggered workflows should cancel superseded runs by default."""
    policy = _load_concurrency_policy()
    false_allowlist = policy["cancel_in_progress_false_allowlist"]

    for workflow_path in _workflow_files():
        triggers = _workflow_triggers(workflow_path)
        if not any(key in triggers for key in _PR_TRIGGER_KEYS):
            continue
        concurrency = _load_workflow(workflow_path)["concurrency"]
        cancel_in_progress = concurrency["cancel-in-progress"]
        if cancel_in_progress is False:
            assert workflow_path.name in false_allowlist, (
                f"{workflow_path.name}: PR-triggered workflow has `cancel-in-progress: false` "
                f"but is not documented in `{_POLICY_PATH.name}`."
            )


def test_pr_workflow_concurrency_groups_are_pr_scoped_or_documented_singletons() -> None:
    """Issue #689: PR concurrency groups must not collapse unrelated PRs by accident."""
    policy = _load_concurrency_policy()
    singleton_allowlist = policy["pr_concurrency_singleton_allowlist"]

    for workflow_path in _workflow_files():
        triggers = _workflow_triggers(workflow_path)
        if not any(key in triggers for key in _PR_TRIGGER_KEYS):
            continue
        if workflow_path.name in singleton_allowlist:
            continue
        concurrency = _load_workflow(workflow_path)["concurrency"]
        group = concurrency["group"]
        assert any(token in group for token in _PR_GROUP_TOKENS), (
            f"{workflow_path.name}: PR-triggered workflow uses concurrency group `{group}` "
            "without a PR/ref discriminator. Either scope it by PR/ref or document it in "
            f"`{_POLICY_PATH.name}` as an intentional singleton."
        )


def test_ci_triage_runbook_documents_workflow_concurrency_policy() -> None:
    """Issue #689: operators need one canonical reference for concurrency rules."""
    runbook = (Path(__file__).parent.parent / "docs" / "runbooks" / "ci-failure-triage.md").read_text(encoding="utf-8")

    assert "workflow_concurrency_policy.json" in runbook
    assert "cancel-in-progress: true" in runbook
    assert "PR number" in runbook or "github.ref" in runbook


def test_lint_workflow_references_documented_concurrency_policy() -> None:
    """Issue #689: workflow-only PRs should enforce the same exception policy in lint."""
    lint_workflow = (_WORKFLOWS_DIR / "lint-workflow-files.yml").read_text(encoding="utf-8")

    assert "workflow_concurrency_policy.json" in lint_workflow


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


def test_workflow_linter_false_cancel_allowlist_matches_static_policy() -> None:
    """The workflow-file linter must enforce the same false-cancel exceptions."""
    text = (_WORKFLOWS_DIR / "lint-workflow-files.yml").read_text(encoding="utf-8")
    policy = _load_concurrency_policy()
    false_allowlist = policy["cancel_in_progress_false_allowlist"]

    assert "workflow_concurrency_policy.json" in text
    for workflow in _CANCEL_FALSE_ALLOWLIST:
        assert workflow in false_allowlist, f"{workflow} missing from workflow concurrency policy"
    for stale_exception in ("publish-artifacts.yml", "publish.yml", "deploy.yml", "nightly-publish.yml"):
        assert stale_exception not in false_allowlist, f"stale broad exception remains in policy: {stale_exception}"


def test_queued_job_reaper_has_safe_stale_controls() -> None:
    """Issue #688: reaper manual runs need dry-run, reason filtering, and caps."""
    data = _load_workflow(_WORKFLOWS_DIR / "util-queued-job-reaper.yml")
    dispatch = _triggers(data)["workflow_dispatch"]
    inputs = dispatch["inputs"]

    assert inputs["dry-run"]["default"] == "true"
    assert inputs["reason-filter"]["default"] == ""
    assert inputs["max-cancel"]["default"] == "10"

    text = (_WORKFLOWS_DIR / "util-queued-job-reaper.yml").read_text(encoding="utf-8")
    assert "unsatisfiable_runner_labels" in text
    assert "superseded_pr_head" in text
    assert "skipped (max-cancel reached)" in text
    assert "QUEUED_JOB_REAPER_DISABLED" in text


def test_anti_phantom_guard_uses_rest_file_listing() -> None:
    """Fleet runners may have older gh versions without `pr diff --name-only`."""
    text = (_WORKFLOWS_DIR / "anti-phantom-merge.yml").read_text(encoding="utf-8")

    assert "gh pr diff" not in text
    assert 'gh api --paginate "repos/$REPO/pulls/$PR/files"' in text
    assert "--jq '.[].filename'" in text
