"""Hermetic tests for scripts/queued_job_diagnostic.py (#2897)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SHARED = Path(__file__).resolve().parents[1] / "shared_scripts"
_SPEC = importlib.util.spec_from_file_location("queued_job_diagnostic", _SHARED / "queued_job_diagnostic.py")
assert _SPEC and _SPEC.loader
diag = importlib.util.module_from_spec(_SPEC)
sys.modules["queued_job_diagnostic"] = diag
_SPEC.loader.exec_module(diag)

pytestmark = pytest.mark.unit

_OCT25 = {
    "utilization": {
        "busy_fraction": 0.577,
        "idle_fraction": 0.387,
        "median_inter_job_gap_seconds": 120,
        "jobs_under_60s_fraction": 0.82,
        "label_and_repo_access_minutes": 82,
    },
    "runners": [
        {"id": 101, "status": "online", "busy": True, "labels": [{"name": "self-hosted"}, {"name": "Linux"}, {"name": "X64"}, {"name": "d-sorg-fleet"}, {"name": "d-sorg-fleet-fast-io"}]},
        {"id": 102, "status": "online", "busy": False, "labels": [{"name": "self-hosted"}, {"name": "Linux"}, {"name": "X64"}, {"name": "d-sorg-fleet"}, {"name": "d-sorg-fleet-fast-io"}]},
        {"id": 103, "status": "online", "busy": True, "labels": [{"name": "self-hosted"}, {"name": "Linux"}, {"name": "X64"}, {"name": "d-sorg-fleet"}, {"name": "d-sorg-fleet-merge-group"}]},
    ],
    "runner_groups": {
        "runner_groups": [
            {"name": "d-sorg-fleet-fast-io", "visibility": "all"},
            {"name": "d-sorg-fleet-restricted", "visibility": "selected", "selected_repositories": [{"name": "Runner_Dashboard"}]},
        ]
    },
    "queued_jobs": [
        {"repo": "Repository_Management", "run_id": 9001, "workflow": "ci", "job": "backend", "labels": ["self-hosted", "Linux", "X64", "d-sorg-fleet", "d-sorg-fleet-fast-io"]},
        {"repo": "Runner_Dashboard", "run_id": 9002, "workflow": "ci", "job": "frontend", "labels": ["self-hosted", "Linux", "X64", "d-sorg-fleet", "d-sorg-fleet-fast-io"]},
        {"repo": "Maxwell_Daemon", "run_id": 9003, "workflow": "ci", "job": "tests", "labels": ["self-hosted", "Linux", "X64", "d-sorg-fleet", "d-sorg-fleet-fast-io"]},
        {"repo": "Some_Fork", "run_id": 9004, "workflow": "ci", "job": "lint", "labels": ["self-hosted", "Linux", "X64", "d-sorg-fleet", "d-sorg-fleet-restricted"]},
        {"repo": "Runner_Dashboard", "run_id": 9005, "workflow": "merge-group", "job": "merge", "labels": ["self-hosted", "Linux", "X64", "d-sorg-fleet", "d-sorg-fleet-merge-group"]},
        {"repo": "Runner_Dashboard", "run_id": 9006, "workflow": "ci", "job": "needs-gate", "labels": ["self-hosted", "Linux", "X64", "d-sorg-fleet"], "blocked_by_needs": True},
        {"repo": "Runner_Dashboard", "run_id": 9007, "workflow": "ci", "job": "concurrency-gate", "labels": ["self-hosted", "Linux", "X64", "d-sorg-fleet"], "blocked_by_concurrency": True},
        {"repo": "Runner_Dashboard", "run_id": 9008, "workflow": "ci", "job": "bad-label", "labels": ["self-hosted", "Linux", "X64", "d-sorg-fleet-retired-label"]},
    ],
}


def _analyze() -> dict:
    return diag.analyze_snapshot(
        queued_jobs=_OCT25["queued_jobs"],
        runners=_OCT25["runners"],
        runner_groups=_OCT25["runner_groups"],
        utilization=_OCT25["utilization"],
    )


def test_oct25_classification_and_idle_explanation() -> None:
    report = _analyze()
    c = report["classification_counts"]
    assert c["waiting_for_runner"] >= 2 and c["runner_group_access"] == 1
    assert c["needs_blocked"] == 1 and c["concurrency_blocked"] == 1 and c["label_mismatch"] == 1
    idle = report["idle_explanation"]
    assert idle["dominant_cause"] == "inter_job_gap"
    assert abs(idle["explained_idle_fraction"] - 0.387) <= 0.10
    assert idle["label_and_repo_access_minutes"] == 82


def test_f5_gate_oct25_defers_reserved_label() -> None:
    f5 = _analyze()["f5_reserved_merge_group_label"]
    assert f5["proceed"] is False
    assert f5["merge_group_jobs_waiting"] >= 1


def test_runner_group_report_lists_restricted_repos() -> None:
    restricted = next(g for g in _analyze()["runner_groups"] if g["name"] == "d-sorg-fleet-restricted")
    assert "Runner_Dashboard" in restricted["repos_allowed"]
    assert restricted["queued_jobs_waiting"] >= 1


def test_f5_proceeds_when_merge_group_capacity_starved() -> None:
    jobs = [
        {"repo": "Runner_Dashboard", "run_id": 1, "workflow": "merge-group", "job": "merge", "labels": ["self-hosted", "Linux", "X64", "d-sorg-fleet", "d-sorg-fleet-merge-group"]},
        {"repo": "Runner_Dashboard", "run_id": 2, "workflow": "merge-group", "job": "merge", "labels": ["self-hosted", "Linux", "X64", "d-sorg-fleet", "d-sorg-fleet-merge-group"]},
    ]
    runners = [{"id": 1, "status": "online", "busy": True, "labels": [{"name": "self-hosted"}, {"name": "Linux"}, {"name": "X64"}, {"name": "d-sorg-fleet"}, {"name": "d-sorg-fleet-merge-group"}]}]
    assert diag.analyze_snapshot(queued_jobs=jobs, runners=runners)["f5_reserved_merge_group_label"]["proceed"] is True


def test_analyze_requires_jobs() -> None:
    with pytest.raises(diag.DiagnosticPreconditionError):
        diag.analyze_snapshot(queued_jobs=[], runners=[])


def test_cli_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "jobs.json").write_text(json.dumps(_OCT25["queued_jobs"]), encoding="utf-8")
    (tmp_path / "runners.json").write_text(json.dumps(_OCT25["runners"]), encoding="utf-8")
    assert diag.main(["--queued-jobs", str(tmp_path / "jobs.json"), "--runners", str(tmp_path / "runners.json")]) == 0
    assert "classification_counts" in json.loads(capsys.readouterr().out)


def test_bare_fleet_label_classified_as_self_hosted_not_github_hosted() -> None:
    runners = [
        {
            "id": 217,
            "status": "online",
            "busy": False,
            "labels": [{"name": "self-hosted"}, {"name": "Linux"}, {"name": "X64"}, {"name": "d-sorg-fleet"}],
        }
    ]
    jobs = [
        {
            "repo": "Runner_Dashboard",
            "run_id": 40979623189,
            "workflow": "Frontend Tests",
            "job": "Playwright E2E smoke tests",
            "labels": ["d-sorg-fleet-public-heavy"],
        },
        {
            "repo": "Runner_Dashboard",
            "run_id": 40979623190,
            "workflow": "Frontend Tests",
            "job": "frontend-required",
            "labels": ["d-sorg-fleet"],
        },
        {
            "repo": "Runner_Dashboard",
            "run_id": 40979623191,
            "workflow": "Frontend Tests",
            "job": "Vitest + Coverage",
            "labels": ["ubuntu-latest"],
        },
    ]
    report = diag.analyze_snapshot(queued_jobs=jobs, runners=runners)
    by_job = {j["job"]: j for j in report["jobs"]}
    assert by_job["Playwright E2E smoke tests"]["reason"] == "label_mismatch"
    assert by_job["frontend-required"]["reason"] == "waiting_for_runner"
    assert by_job["Vitest + Coverage"]["reason"] == "github_hosted"

