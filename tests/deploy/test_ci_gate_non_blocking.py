"""Bot-maintenance check-runs must not block node auto-deploy."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from node_auto_deploy.ci_gate import (  # noqa: E402
    NON_BLOCKING_DEPLOY_CHECK_NAMES,
    ci_gate_for_deploy_commit,
)
from required_context_helper import success_runs_for_required_contexts  # noqa: E402

_SHA = "a" * 40


def _run(name: str, status: str, conclusion: str | None, idx: int) -> dict[str, object]:
    return {
        "id": idx,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "head_sha": _SHA,
        "app": {"id": 1},
        "check_suite": {"id": idx},
        "started_at": "2026-01-01T00:00:00Z",
    }


def test_queued_verify_closure_does_not_block_deploy() -> None:
    runs = [dict(r, id=i) for i, r in enumerate(success_runs_for_required_contexts(_SHA), start=1)]
    runs.append(_run("verify-closure", "queued", None, len(runs) + 1))
    ok, detail = ci_gate_for_deploy_commit(runs, _SHA)
    assert ok is True, detail


def test_cancelled_bot_jobs_do_not_block_deploy() -> None:
    runs = [dict(r, id=i) for i, r in enumerate(success_runs_for_required_contexts(_SHA), start=1)]
    bot_names = ["verify-closure", "reap", "sweep"]
    runs += [_run(n, "completed", "cancelled", len(runs) + i + 1) for i, n in enumerate(bot_names)]
    ok, detail = ci_gate_for_deploy_commit(runs, _SHA)
    assert ok is True, detail


def test_real_quality_check_still_blocks() -> None:
    runs = [dict(r, id=i) for i, r in enumerate(success_runs_for_required_contexts(_SHA), start=1)]
    for run in runs:
        if run.get("name") == "quality-gate":
            run["conclusion"] = "failure"
    runs.append(_run("verify-closure", "completed", "success", len(runs) + 1))
    ok, detail = ci_gate_for_deploy_commit(runs, _SHA)
    assert ok is False
    assert "quality-gate" in detail


def test_verify_closure_listed() -> None:
    assert "verify-closure" in NON_BLOCKING_DEPLOY_CHECK_NAMES


def test_failed_shepherd_check_does_not_block_deploy() -> None:
    runs = [
        _run("quality-gate", "completed", "success", 1),
        _run("shepherd", "completed", "failure", 2),
    ]
    ok, detail = ci_gate_for_deploy_commit(runs, _SHA)
    assert ok is True, detail
    assert "shepherd" in NON_BLOCKING_DEPLOY_CHECK_NAMES


def test_failed_queue_slo_check_does_not_block_release_or_deploy() -> None:
    from main_auto_release.gate import commit_passes_publisher_ci_gate

    runs = [dict(r, id=i) for i, r in enumerate(success_runs_for_required_contexts(_SHA), start=1)]
    runs.append(_run("slo", "completed", "failure", len(runs) + 1))
    ok, detail = ci_gate_for_deploy_commit(runs, _SHA)
    assert ok is True, detail
    passed, publisher_detail = commit_passes_publisher_ci_gate(runs, _SHA)
    assert passed, publisher_detail


@pytest.mark.parametrize("status,conclusion", [("in_progress", None), ("completed", "failure")])
def test_split_publisher_gate_never_waits_on_itself(status: str, conclusion: str | None) -> None:
    from main_auto_release.gate import commit_passes_publisher_ci_gate, publisher_gate_failure_is_terminal

    runs = [dict(r, id=i) for i, r in enumerate(success_runs_for_required_contexts(_SHA), start=1)]
    runs.append(
        _run("Wait for CI gate (cancellable; newer candidate supersedes)", status, conclusion, len(runs) + 1),
    )
    passed, detail = commit_passes_publisher_ci_gate(runs, _SHA)
    assert passed, detail
    assert ci_gate_for_deploy_commit(runs, _SHA)[0]
    assert not publisher_gate_failure_is_terminal([runs[-1]], _SHA)


@pytest.mark.parametrize("status,conclusion", [("in_progress", None), ("completed", "failure")])
def test_source_checks_still_block_with_a_passing_publisher(status: str, conclusion: str | None) -> None:
    runs = [dict(r, id=i) for i, r in enumerate(success_runs_for_required_contexts(_SHA), start=1)]
    for run in runs:
        if run.get("name") == "quality-gate":
            run["status"] = status
            run["conclusion"] = conclusion
    runs.append(
        _run(
            "Wait for CI gate (cancellable; newer candidate supersedes)",
            "completed",
            "success",
            len(runs) + 1,
        ),
    )
    passed, detail = ci_gate_for_deploy_commit(runs, _SHA)
    assert not passed
    assert "quality-gate" in detail


def test_publisher_workflow_job_names_match_self_reference_exclusions() -> None:
    workflow = yaml.safe_load((REPO / ".github/workflows/main-ci-auto-release.yml").read_text())
    names = {job["name"] for job in workflow["jobs"].values()}
    assert names <= NON_BLOCKING_DEPLOY_CHECK_NAMES
