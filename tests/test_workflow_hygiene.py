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
    "deploy-qualified-release.yml": "A root transaction must run to commit or rollback; never interrupt it mid-stream.",
    "release.yml": "Publishes release artifacts and tags; never interrupt a release mid-stream.",
}

_SINGLETON_GROUP_ALLOWLIST: dict[str, str] = {
    "Agent-Fleet-Dashboard.yml": "Scheduled singleton refresh; a newer refresh supersedes older work.",
    "Agent-Lease-Reaper.yml": "Scheduled singleton cleanup; overlapping runs should collapse.",
    "agent-panel-review.yml": "One panel review worker should own the review queue at a time.",
    "Agent-Redundant-PR-Closer.yml": "Singleton closer avoids competing PR-close decisions.",
    "ci-nightly.yml": "Nightly singleton; only one nightly run should occupy the fleet.",
    "issue-taxonomy-backfill.yml": "Backfill is an explicit singleton maintenance workflow.",
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


def test_the_jules_remediation_suite_stays_retired() -> None:
    """Issues #596/#597, closed out by Repository_Management#1483.

    Those issues asked that automatic CI remediation have exactly one owner, and
    pinned three contracts onto ``Jules-PR-AutoFix.yml`` and
    ``Jules-Control-Tower.yml``: no direct ``workflow_run`` trigger, bounded REST
    verification instead of ``gh pr checks`` polling, and a protected-branch
    guard. All three workflows have since been retired - unowned, last run a
    failure or cancellation, and CI repair in this fleet is now done by the
    Claude and Codex remediation agents, not by the Jules suite.

    The requirement outlives the files, so the assertion is inverted rather than
    deleted: there must be no automatic CI remediation entrypoint here at all.
    Reintroducing one is a deliberate act that should fail this test first.
    """
    for name in (
        "Jules-PR-AutoFix.yml",
        "Jules-Control-Tower.yml",
        "Jules-Auto-Repair.yml",
    ):
        assert not (_WORKFLOWS_DIR / name).exists(), (
            f"{name} was retired by Repository_Management#1483; reinstating it "
            "needs an owner and a fresh decision, not a silent restore"
        )


def test_workflow_linter_false_cancel_allowlist_matches_static_policy() -> None:
    """The workflow-file linter must enforce the same false-cancel exceptions."""
    text = (_WORKFLOWS_DIR / "lint-workflow-files.yml").read_text(encoding="utf-8")
    policy = _load_concurrency_policy()
    false_allowlist = policy["cancel_in_progress_false_allowlist"]

    assert "workflow_concurrency_policy.json" in text
    for workflow in _CANCEL_FALSE_ALLOWLIST:
        assert workflow in false_allowlist, f"{workflow} missing from workflow concurrency policy"
    for stale_exception in (
        "publish-artifacts.yml",
        "publish.yml",
        "deploy.yml",
        "nightly-publish.yml",
    ):
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


def test_anti_phantom_guard_uses_reversible_public_fast_lane() -> None:
    """A lightweight public guard must not stall when local pools are drained."""
    workflow = _load_workflow(_WORKFLOWS_DIR / "anti-phantom-merge.yml")
    runs_on = str(workflow["jobs"]["guard"]["runs-on"])

    assert "ubuntu-latest" in runs_on
    assert "CI_RUNNER_MODE" in runs_on
    assert "d-sorg-fleet" in runs_on


def test_anti_phantom_guard_recognizes_dashboard_code_roots() -> None:
    """Feature PR checks must include this repo's real app roots."""
    workflow = (_WORKFLOWS_DIR / "anti-phantom-merge.yml").read_text(encoding="utf-8")
    action = (
        Path(__file__).parent.parent / ".github" / "actions" / "verify-issue-resolution" / "action.yml"
    ).read_text(encoding="utf-8")

    for text in (workflow, action):
        assert "backend/" in text
        assert "frontend/src/" in text
        assert "backend|frontend/src|src|tests|rust_core|api" in text


def test_pre_push_mypy_dependencies_are_installable() -> None:
    """The mypy pre-push hook must not depend on removed or nonexistent packages."""
    text = (Path(__file__).parent.parent / ".pre-commit-config.yaml").read_text(encoding="utf-8")

    assert "psutil-stubs" not in text
    assert "types-psutil" in text
    assert "language: system" in text
    assert '"tests/"' in text
    assert '"-ll"' in text
    assert '"-ii"' in text


_REQUIRED_CHECKS_POLICY = Path(__file__).parent.parent / "config" / "required_status_checks_policy.json"


def _required_contexts() -> set[str]:
    data = json.loads(_REQUIRED_CHECKS_POLICY.read_text(encoding="utf-8"))
    contexts = {entry["context"] for entry in data["required_contexts"]}
    assert contexts, "required_status_checks_policy.json lists no contexts"
    return contexts


def test_required_context_workflows_have_no_pull_request_path_filters() -> None:
    """A required status check that cannot run is a permanent merge deadlock.

    Branch protection waits for every required context to report. A path
    filter on ``pull_request`` skips the whole workflow when a PR touches only
    filtered paths, so the context never reports at all - the PR sits BLOCKED
    with zero failures and nothing pending, forever. It is indistinguishable
    from a check that simply has not started yet.

    ``ci-standard.yml`` already documented this reasoning for ``**.md`` but
    never applied it to the remaining entries, so a ``.gitignore``-only or
    ``LICENSE``-only PR was unmergeable.

    ``push`` filters are unaffected - post-merge CI does not gate the required
    context. Filtering by ``types`` is fine too: it cannot skip a workflow
    based on which files a PR touches.
    """
    required = _required_contexts()

    for path in sorted(_WORKFLOWS_DIR.glob("*.yml")):
        data = _load_workflow(path)
        jobs = data.get("jobs") or {}
        provided = required & set(jobs)
        if not provided:
            continue

        triggers = _triggers(data)
        if not isinstance(triggers, dict):
            continue
        pull_request = triggers.get("pull_request")
        if not isinstance(pull_request, dict):
            continue

        for key in ("paths", "paths-ignore"):
            assert not pull_request.get(key), (
                f"{path.name} provides required status check(s) "
                f"{sorted(provided)} but filters its `pull_request` trigger on "
                f"`{key}: {pull_request[key]}`. A PR touching only those paths "
                "skips the workflow, so the required context never reports and "
                "the PR can never merge. Remove the filter (keep it on `push` "
                "if post-merge cost matters)."
            )


def test_required_context_workflows_run_in_the_merge_queue() -> None:
    """Every workflow that reports a required context must trigger on merge_group.

    With the merge queue enabled on ``main`` (Repository_Management#1889 /
    #1890), GitHub waits for every required context on the ``merge_group``
    run. A workflow without that trigger never reports there, so every queued
    PR would wait until the queue's check timeout and then be ejected.
    """
    data = json.loads(_REQUIRED_CHECKS_POLICY.read_text(encoding="utf-8"))
    sources = {entry["source_workflow"] for entry in data["required_contexts"]}

    found: set[str] = set()
    for path in sorted(_WORKFLOWS_DIR.glob("*.yml")):
        workflow = _load_workflow(path)
        if workflow.get("name") not in sources:
            continue
        found.add(workflow["name"])
        assert _has_trigger(workflow, "merge_group"), (
            f"{path.name} reports a required status check but has no "
            "`merge_group:` trigger, so the merge queue would wait for it forever."
        )

    assert found == sources, f"no workflow file found for {sorted(sources - found)}"


# ---------------------------------------------------------------------------
# Event-tiered CI Standard (issue #1864, sibling of Repository_Management#1915)
#
# pull_request  -> PR tier: lint, format, type check and fast tests.
# merge_group   -> full tier: everything (the authoritative gate).
# push to main  -> post-merge tier: no duplicate heavy jobs; the squash commit
#                  is the tree the merge queue already tested.
# A `changes` job decides the tier and, on pull_request only, whether the PR
# touches any Python surface (the docs-only fast path). `quality-gate` and
# `tests-required` report on every event and fail closed.
# ---------------------------------------------------------------------------

import os  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

_CI_STANDARD = _WORKFLOWS_DIR / "ci-standard.yml"
_HEAVY_FULL_TIER_JOBS = ("security-scan",)
_PYTHON_LANE_JOBS = ("lint", "tests")


def _ci_jobs() -> dict:
    jobs = _load_workflow(_CI_STANDARD)["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _ci_step(job_id: str, *, name: str | None = None, step_id: str | None = None) -> dict:
    for step in _ci_jobs()[job_id]["steps"]:
        if name is not None and step.get("name") == name:
            return step
        if step_id is not None and step.get("id") == step_id:
            return step
    raise AssertionError(f"ci-standard.yml job {job_id!r} has no step name={name!r} id={step_id!r}")


def _run_step(step: dict, env: dict[str, str]) -> tuple[int, dict[str, str]]:
    """Execute a workflow `run:` script under bash the way Actions does.

    Returns the exit code and whatever the script appended to $GITHUB_OUTPUT.
    """
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "github_output"
        output_path.write_text("", encoding="utf-8")
        full_env = {"PATH": os.environ.get("PATH", ""), "GITHUB_OUTPUT": str(output_path), **env}
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
            env=full_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        outputs: dict[str, str] = {}
        for line in output_path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                outputs[key] = value
    return result.returncode, outputs


def _tier_for(event_name: str) -> dict[str, str]:
    code, outputs = _run_step(_ci_step("changes", step_id="tier"), {"EVENT_NAME": event_name})
    assert code == 0, f"tier step failed for {event_name}"
    return outputs


def test_ci_standard_keeps_every_trigger() -> None:
    """The tiers depend on all three events still reaching the workflow."""
    data = _load_workflow(_CI_STANDARD)
    for event in ("pull_request", "merge_group", "push", "workflow_dispatch"):
        assert _has_trigger(data, event), f"ci-standard.yml lost its `{event}` trigger"


def test_changes_job_runs_on_every_event() -> None:
    """The tier decision must exist on every event, or downstream gates go blind."""
    changes = _ci_jobs()["changes"]
    assert "if" not in changes, "`changes` must not be conditional; every gate reads its outputs"
    outputs = changes["outputs"]
    for key in ("tier", "full_suite", "run_python_tests"):
        assert key in outputs, f"`changes` must output {key!r}"


@pytest.mark.parametrize(
    ("event_name", "tier", "full_suite", "run_python_tests"),
    [
        ("pull_request", "pr", "false", None),  # decided by the scope detector
        ("merge_group", "full", "true", "true"),
        ("workflow_dispatch", "full", "true", "true"),
        ("push", "post-merge", "false", "false"),
        ("schedule", "full", "true", "true"),  # unknown events fail safe to full
    ],
)
def test_tier_per_event(event_name: str, tier: str, full_suite: str, run_python_tests: str | None) -> None:
    outputs = _tier_for(event_name)
    assert outputs.get("tier") == tier
    assert outputs.get("full_suite") == full_suite
    assert outputs.get("run_python_tests") == run_python_tests


def test_docs_only_scope_detector_runs_on_pull_request_only() -> None:
    """The docs-only fast path is a PR optimisation; queue groups can mix PRs."""
    scope = _ci_step("changes", step_id="python-scope")
    assert scope.get("if") == "github.event_name == 'pull_request'", (
        "the docs-only detector must never run (and so never skip) outside pull_request"
    )
    assert _tier_for("merge_group")["run_python_tests"] == "true", "merge_group must never skip the Python lane"


def test_scope_detector_fails_closed_on_truncated_file_listing() -> None:
    """The files API returns at most 100 entries per page; a truncated list is not proof of docs-only."""
    scope = _ci_step("changes", step_id="python-scope")["run"]
    assert "len(files) >= 100" in scope


def test_python_lane_jobs_gate_on_changes_job() -> None:
    jobs = _ci_jobs()
    for job_id in _PYTHON_LANE_JOBS:
        condition = str(jobs[job_id].get("if", ""))
        assert "needs.changes.outputs.run_python_tests == 'true'" in condition, job_id
        assert "changes" in jobs[job_id]["needs"], job_id


def test_heavy_jobs_run_only_in_the_full_tier() -> None:
    jobs = _ci_jobs()
    for job_id in _HEAVY_FULL_TIER_JOBS:
        condition = str(jobs[job_id].get("if", ""))
        assert "needs.changes.outputs.full_suite == 'true'" in condition, (
            f"{job_id} is a heavy scan; it belongs to the merge_group tier, not every PR push"
        )
    for step_name in ("Run bandit security scan", "Security Audit (pip-audit)"):
        step = _ci_step("lint", name=step_name)
        assert step.get("if") == "needs.changes.outputs.full_suite == 'true'", step_name


def test_pr_tier_runs_fast_tests_and_full_tier_measures_coverage() -> None:
    step = _ci_step("tests", name="Run Python tests")
    assert step["env"]["FULL_SUITE"] == "${{ needs.changes.outputs.full_suite }}"
    run = step["run"]
    assert "--cov=backend" in run
    assert 'if [ "$FULL_SUITE" = "true" ]' in run
    assert "not slow" in run, "the PR tier runs the fast subset"


@pytest.mark.parametrize("job_id", ["quality-gate", "tests-required"])
def test_required_aggregates_report_on_every_event(job_id: str) -> None:
    from scripts.check_required_checks_drift import check_job_fails_closed

    job = _ci_jobs()[job_id]
    assert job.get("if") == "always()", f"{job_id} must run on every event"
    assert "changes" in job["needs"], f"{job_id} must read the tier decision"
    assert check_job_fails_closed(_CI_STANDARD.read_text(encoding="utf-8"), job_id) == []


def _quality_gate(**env: str) -> int:
    defaults = {
        "CHANGES": "success",
        "HEALTH": "success",
        "TIER": "pr",
        "FULL_SUITE": "false",
        "RUN_PYTHON_TESTS": "true",
        "RESULT_LINT": "success",
        "RESULT_TESTS": "success",
        "RESULT_SECURITY": "skipped",
    }
    code, _ = _run_step(_ci_step("quality-gate", name="Require every gated job to succeed"), {**defaults, **env})
    return code


def _tests_required(**env: str) -> int:
    defaults = {
        "CHANGES": "success",
        "HEALTH": "success",
        "TIER": "pr",
        "RUN_PYTHON_TESTS": "true",
        "TESTS": "success",
    }
    code, _ = _run_step(_ci_step("tests-required", name="Confirm Python test matrix"), {**defaults, **env})
    return code


def test_quality_gate_passes_the_pr_tier_without_heavy_scans() -> None:
    assert _quality_gate() == 0


def test_quality_gate_requires_heavy_scans_in_the_full_tier() -> None:
    full = {"TIER": "full", "FULL_SUITE": "true"}
    assert _quality_gate(**full, RESULT_SECURITY="success") == 0
    assert _quality_gate(**full, RESULT_SECURITY="skipped") == 1
    assert _quality_gate(**full, RESULT_SECURITY="failure") == 1


def test_quality_gate_passes_docs_only_pr_and_post_merge_push() -> None:
    skipped = {"RESULT_LINT": "skipped", "RESULT_TESTS": "success", "RESULT_SECURITY": "skipped"}
    assert _quality_gate(**skipped, RUN_PYTHON_TESTS="false") == 0
    assert _quality_gate(**skipped, RUN_PYTHON_TESTS="false", TIER="post-merge") == 0


def test_quality_gate_fails_closed() -> None:
    assert _quality_gate(CHANGES="failure", RUN_PYTHON_TESTS="") == 1
    assert _quality_gate(HEALTH="failure") == 1
    assert _quality_gate(RESULT_LINT="failure") == 1
    assert _quality_gate(RESULT_LINT="skipped") == 1, "lint may only skip when the Python lane is off"
    assert _quality_gate(RESULT_TESTS="failure") == 1


def test_tests_required_reports_on_every_tier() -> None:
    assert _tests_required() == 0
    assert _tests_required(TIER="full") == 0
    assert _tests_required(RUN_PYTHON_TESTS="false", TESTS="skipped") == 0
    assert _tests_required(TIER="post-merge", RUN_PYTHON_TESTS="false", TESTS="skipped") == 0


def test_tests_required_fails_closed() -> None:
    assert _tests_required(CHANGES="failure", RUN_PYTHON_TESTS="", TESTS="skipped") == 1
    assert _tests_required(HEALTH="failure") == 1
    assert _tests_required(TESTS="failure") == 1
    assert _tests_required(TESTS="skipped") == 1, "a skipped matrix only passes when the lane is off"
    assert _tests_required(TIER="full", RUN_PYTHON_TESTS="true", TESTS="skipped") == 1
