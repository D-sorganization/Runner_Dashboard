"""Contract: bulk-tier governance jobs must not use the Desk-neutral label.

Owner ruling 2026-11-14: lightweight checks (pr-guard, spec/title/phantom guards,
secret scans, merge-queue hygiene) route to ``d-sorg-fleet-bulk``, not the neutral ``d-sorg-fleet`` hub pool label.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
POLICY = REPO_ROOT / "config" / "workflow_runner_routing_policy.json"

DESK_NEUTRAL_MARKERS = (
    "|| 'd-sorg-fleet' }}",
    "[self-hosted, d-sorg-fleet]",
    "[self-hosted, Linux, X64, d-sorg-fleet]",
)

BULK_GOVERNANCE_WORKFLOWS = frozenset(
    {
        "anti-phantom-merge.yml",
        "ci-private-roles-redaction-gate.yml",
        "ci-secrets.yml",
        "ci-spec-check.yml",
        "ci-standalone-smoke.yml",
        "lint-workflow-files.yml",
        "local-only-runner-guard.yml",
        "merge-shepherd.yml",
        "publish-guard.yml",
        "rd-barb-hold-integrity.yml",
        "recurring-scan.yml",
        "reusable-pr-freshness-guard.yml",
        "architecture-map-contract.yml",
    }
)

PR_GUARD_BULK_RUNS_ON = ["self-hosted", "Linux", "X64", "d-sorg-fleet-bulk"]


def _policy_bulk_workflows() -> set[str]:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    bulk = policy.get("workflow_classes", {}).get("bulk", {})
    return set(bulk.get("workflows") or [])


def test_bulk_policy_lists_reusable_pr_guard() -> None:
    assert "reusable-pr-freshness-guard.yml" in _policy_bulk_workflows()


def test_pr_freshness_guard_pinned_to_bulk_tuple() -> None:
    data = yaml.safe_load((WORKFLOWS / "reusable-pr-freshness-guard.yml").read_text(encoding="utf-8"))
    assert data["jobs"]["guard"]["runs-on"] == PR_GUARD_BULK_RUNS_ON


def test_bulk_governance_workflows_avoid_desk_neutral_label() -> None:
    missing = []
    for name in sorted(BULK_GOVERNANCE_WORKFLOWS):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        for marker in DESK_NEUTRAL_MARKERS:
            if marker in text:
                missing.append(f"{name}: found {marker!r}")
    assert not missing, "\n".join(missing)


def test_docker_build_lightweight_jobs_use_bulk_not_desk() -> None:
    data = yaml.safe_load((WORKFLOWS / "docker-build.yml").read_text(encoding="utf-8"))
    for job_name in ("queue-entry-alive", "decide-docker-scope"):
        runs_on = str(data["jobs"][job_name]["runs-on"])
        assert "d-sorg-fleet-bulk" in runs_on, job_name
        assert "|| 'd-sorg-fleet' }}" not in runs_on, job_name


def test_ci_auto_deploy_merge_queue_guard_uses_bulk() -> None:
    data = yaml.safe_load((WORKFLOWS / "ci-auto-deploy-regression.yml").read_text(encoding="utf-8"))
    runs_on = str(data["jobs"]["queue-entry-alive"]["runs-on"])
    assert "d-sorg-fleet-bulk" in runs_on
    assert "|| 'd-sorg-fleet' }}" not in runs_on