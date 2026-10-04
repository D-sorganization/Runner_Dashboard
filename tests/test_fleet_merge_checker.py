"""Unit tests for fleet merge settings drift checker (RD#1850, RM#1890, RM#1900)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fleet_merge_checker import (
    DEFAULT_POLICY_PATH,
    check_disallowed_workflows,
    check_fleet,
    check_merge_group_triggers,
    check_merge_queue_ruleset,
    check_repo_settings,
    check_single_repo,
    check_up_to_date_protection,
    format_drift_issue,
    load_policy,
    workflow_has_merge_group_trigger,
)


@pytest.fixture
def sample_policy() -> dict[str, Any]:
    return {
        "version": 1,
        "repositories": ["D-sorganization/Runner_Dashboard", "D-sorganization/UpstreamDrift"],
        "default_branch": "main",
        "require_branches_up_to_date": False,
        "repo_settings": {
            "allow_auto_merge": True,
            "allow_squash_merge": True,
            "delete_branch_on_merge": True,
        },
        "merge_queue": {
            "required": True,
            "parameters": {
                "check_response_timeout_minutes": 180,
                "grouping_strategy": "ALLGREEN",
                "max_entries_to_build": 5,
                "max_entries_to_merge": 5,
                "merge_method": "SQUASH",
                "min_entries_to_merge": 1,
                "min_entries_to_merge_wait_minutes": 5,
            },
        },
        "workflows": {
            "require_merge_group_on_required_checks": True,
            "disallowed_workflows": ["Auto-Update PRs"],
        },
    }


@pytest.fixture
def contracts_dir() -> Path:
    return Path(__file__).resolve().parent / "contracts"


@pytest.fixture
def valid_rulesets(contracts_dir: Path) -> list[dict[str, Any]]:
    return json.loads((contracts_dir / "rulesets_snapshot_fixed_example.json").read_text(encoding="utf-8"))


@pytest.fixture
def valid_protection(contracts_dir: Path) -> dict[str, Any]:
    return json.loads((contracts_dir / "branch_protection_snapshot_merge_queue.json").read_text(encoding="utf-8"))


def test_default_policy_file_exists_and_lists_expected_fleet_repos() -> None:
    policy = load_policy(DEFAULT_POLICY_PATH)
    assert policy["version"] == 1
    assert policy["require_branches_up_to_date"] is False
    assert policy["merge_queue"]["required"] is True
    assert "D-sorganization/Runner_Dashboard" in policy["repositories"]
    assert "D-sorganization/UpstreamDrift" in policy["repositories"]
    assert "D-sorganization/AffineDrift" in policy["repositories"]
    assert "D-sorganization/Tools" in policy["repositories"]
    assert "D-sorganization/Tools_Private" in policy["repositories"]
    assert len(policy["repositories"]) == 8


def test_load_policy_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_policy(tmp_path / "non_existent.json")


def test_check_repo_settings_pass(sample_policy: dict[str, Any]) -> None:
    repo_details = {
        "allow_auto_merge": True,
        "allow_squash_merge": True,
        "delete_branch_on_merge": True,
    }
    problems = check_repo_settings(sample_policy, repo_details)
    assert problems == []


def test_check_repo_settings_fail_flags(sample_policy: dict[str, Any]) -> None:
    repo_details = {
        "allow_auto_merge": False,
        "allow_squash_merge": True,
        "delete_branch_on_merge": False,
    }
    problems = check_repo_settings(sample_policy, repo_details)
    assert len(problems) == 2
    assert any("allow_auto_merge" in p for p in problems)
    assert any("delete_branch_on_merge" in p for p in problems)


def test_check_merge_queue_ruleset_pass(sample_policy: dict[str, Any], valid_rulesets: list[dict[str, Any]]) -> None:
    problems = check_merge_queue_ruleset(sample_policy, valid_rulesets)
    assert problems == []


def test_check_merge_queue_ruleset_missing_rule(sample_policy: dict[str, Any]) -> None:
    empty_rulesets = [{"name": "No Queue", "rules": [{"type": "deletion"}]}]
    problems = check_merge_queue_ruleset(sample_policy, empty_rulesets)
    assert len(problems) == 1
    assert "no active ruleset requires a merge queue" in problems[0]


def test_check_merge_queue_ruleset_mismatched_parameter(
    sample_policy: dict[str, Any], valid_rulesets: list[dict[str, Any]]
) -> None:
    mutated = json.loads(json.dumps(valid_rulesets))
    for rs in mutated:
        for r in rs.get("rules", []):
            if r.get("type") == "merge_queue":
                r["parameters"]["check_response_timeout_minutes"] = 15
    problems = check_merge_queue_ruleset(sample_policy, mutated)
    assert len(problems) == 1
    assert "check_response_timeout_minutes" in problems[0]


def test_check_up_to_date_protection_pass(
    sample_policy: dict[str, Any],
    valid_protection: dict[str, Any],
    valid_rulesets: list[dict[str, Any]],
) -> None:
    problems = check_up_to_date_protection(sample_policy, valid_protection, valid_rulesets)
    assert problems == []


def test_check_up_to_date_protection_fail_classic(
    sample_policy: dict[str, Any],
    valid_protection: dict[str, Any],
    valid_rulesets: list[dict[str, Any]],
) -> None:
    mutated_prot = json.loads(json.dumps(valid_protection))
    mutated_prot["required_status_checks"]["strict"] = True
    problems = check_up_to_date_protection(sample_policy, mutated_prot, valid_rulesets)
    assert len(problems) == 1
    assert "classic branch protection requires branches to be up to date (strict=true)" in problems[0]


def test_check_up_to_date_protection_fail_ruleset(
    sample_policy: dict[str, Any],
    valid_protection: dict[str, Any],
    valid_rulesets: list[dict[str, Any]],
) -> None:
    mutated_rulesets = json.loads(json.dumps(valid_rulesets))
    for rs in mutated_rulesets:
        for r in rs.get("rules", []):
            if r.get("type") == "required_status_checks":
                r["parameters"]["strict_required_status_checks_policy"] = True
    problems = check_up_to_date_protection(sample_policy, valid_protection, mutated_rulesets)
    assert len(problems) == 1
    assert "requires branches to be up to date (strict=true)" in problems[0]


def test_workflow_has_merge_group_trigger_variants() -> None:
    yaml_string = "on: merge_group\njobs: {}"
    assert workflow_has_merge_group_trigger(yaml_string) is True

    yaml_list = "on: [push, pull_request, merge_group]\njobs: {}"
    assert workflow_has_merge_group_trigger(yaml_list) is True

    yaml_dict = "on:\n  push:\n    branches: [main]\n  merge_group:\njobs: {}"
    assert workflow_has_merge_group_trigger(yaml_dict) is True

    yaml_missing = "on:\n  push:\n    branches: [main]\njobs: {}"
    assert workflow_has_merge_group_trigger(yaml_missing) is False

    assert workflow_has_merge_group_trigger("not valid yaml :::") is False


def test_check_merge_group_triggers_detected(sample_policy: dict[str, Any]) -> None:
    workflows = {
        "ci-standard.yml": "on:\n  push:\n  merge_group:\njobs: {}",
        "docs.yml": "on:\n  push:\njobs: {}",
    }
    required = ["ci-standard.yml", "docs.yml", "missing.yml"]
    problems = check_merge_group_triggers(workflows, required)
    assert len(problems) == 2
    assert any("missing a 'merge_group:' trigger" in p for p in problems)
    assert any("not found in repository" in p for p in problems)


def test_check_disallowed_workflows(sample_policy: dict[str, Any]) -> None:
    existing = ["ci-standard.yml", "util-auto-update-prs.yml", "release.yml"]
    problems = check_disallowed_workflows(sample_policy, existing)
    assert len(problems) == 1
    assert "disallowed workflow 'Auto-Update PRs'" in problems[0]


def test_check_single_repo_pass_and_fail(
    sample_policy: dict[str, Any],
    valid_protection: dict[str, Any],
    valid_rulesets: list[dict[str, Any]],
) -> None:
    good_snapshot = {
        "repo": "D-sorganization/Runner_Dashboard",
        "repo_details": {
            "allow_auto_merge": True,
            "allow_squash_merge": True,
            "delete_branch_on_merge": True,
        },
        "protection": valid_protection,
        "rulesets": valid_rulesets,
        "workflows": {"ci.yml": "on:\n  merge_group:\n"},
        "required_workflows": ["ci.yml"],
        "existing_workflow_names": ["ci.yml"],
    }
    res_pass = check_single_repo(sample_policy, good_snapshot)
    assert res_pass["status"] == "pass"
    assert res_pass["findings"] == []

    bad_snapshot = dict(good_snapshot)
    bad_snapshot["repo_details"] = {"allow_auto_merge": False}
    res_fail = check_single_repo(sample_policy, bad_snapshot)
    assert res_fail["status"] == "fail"
    assert len(res_fail["findings"]) > 0


def test_check_fleet_aggregation(
    sample_policy: dict[str, Any],
    valid_protection: dict[str, Any],
    valid_rulesets: list[dict[str, Any]],
) -> None:
    good_snapshot = {
        "repo_details": {
            "allow_auto_merge": True,
            "allow_squash_merge": True,
            "delete_branch_on_merge": True,
        },
        "protection": valid_protection,
        "rulesets": valid_rulesets,
        "workflows": {},
        "required_workflows": [],
        "existing_workflow_names": [],
    }
    fleet_snapshots = {
        "D-sorganization/Runner_Dashboard": {"repo": "D-sorganization/Runner_Dashboard", **good_snapshot},
        "D-sorganization/UpstreamDrift": {"repo": "D-sorganization/UpstreamDrift", **good_snapshot},
    }
    report = check_fleet(sample_policy, fleet_snapshots)
    assert report["status"] == "pass"
    assert len(report["results"]) == 2

    # If one repo is missing
    del fleet_snapshots["D-sorganization/UpstreamDrift"]
    report_missing = check_fleet(sample_policy, fleet_snapshots)
    assert report_missing["status"] == "fail"
    drift_repo_findings = [
        r["findings"] for r in report_missing["results"] if r["repo"] == "D-sorganization/UpstreamDrift"
    ]
    assert any("snapshot missing" in f[0] for f in drift_repo_findings)


def test_format_drift_issue() -> None:
    findings = [
        "merge queue parameter 'check_response_timeout_minutes' is 15, expected 180",
        "repository flag 'allow_auto_merge' is False, expected True",
    ]
    title, body = format_drift_issue("D-sorganization/UpstreamDrift", findings)
    assert title == "fix(settings): merge-queue and branch-protection drift detected in D-sorganization/UpstreamDrift"
    assert "- [ ] merge queue parameter" in body
    assert "- [ ] repository flag 'allow_auto_merge'" in body
    assert "RM#1900" in body


def test_cli_main_snapshots(
    tmp_path: Path,
    valid_protection: dict[str, Any],
    valid_rulesets: list[dict[str, Any]],
) -> None:
    from scripts.check_fleet_merge_settings import main

    policy_file = tmp_path / "policy.json"
    policy_file.write_text(
        json.dumps(
            {
                "version": 1,
                "repositories": ["D-sorganization/Runner_Dashboard"],
                "require_branches_up_to_date": False,
                "repo_settings": {"allow_auto_merge": True},
                "merge_queue": {"required": False},
            }
        ),
        encoding="utf-8",
    )
    snapshots_dir = tmp_path / "snapshots"
    snapshots_dir.mkdir()
    rd_snapshot = snapshots_dir / "D-sorganization__Runner_Dashboard.json"
    rd_snapshot.write_text(
        json.dumps(
            {
                "repo": "D-sorganization/Runner_Dashboard",
                "repo_details": {"allow_auto_merge": True},
                "protection": valid_protection,
                "rulesets": valid_rulesets,
                "workflows": {},
                "required_workflows": [],
                "existing_workflow_names": [],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["--policy", str(policy_file), "--snapshots-dir", str(snapshots_dir)])
    assert exit_code == 0

    # Mutate to create drift
    rd_snapshot.write_text(
        json.dumps(
            {
                "repo": "D-sorganization/Runner_Dashboard",
                "repo_details": {"allow_auto_merge": False},
                "protection": valid_protection,
                "rulesets": valid_rulesets,
                "workflows": {},
                "required_workflows": [],
                "existing_workflow_names": [],
            }
        ),
        encoding="utf-8",
    )
    exit_code_drift = main(
        [
            "--policy",
            str(policy_file),
            "--snapshots-dir",
            str(snapshots_dir),
            "--format-issue",
        ]
    )
    assert exit_code_drift == 1


def test_get_queue_merge_settings_route(monkeypatch: pytest.MonkeyPatch, sample_policy: dict[str, Any]) -> None:
    import cache_utils
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import queue

    app = FastAPI()
    app.include_router(queue.router)
    client = TestClient(app)

    # Monkeypatch fleet:merge_settings cache
    mock_report = {
        "status": "pass",
        "results": [{"repo": "D-sorganization/Runner_Dashboard", "status": "pass", "findings": []}],
    }
    cache_utils.cache_set("fleet:merge_settings", mock_report)

    response = client.get("/api/queue/merge-settings")
    assert response.status_code == 200
    assert response.json()["status"] == "pass"
    assert response.json()["results"][0]["repo"] == "D-sorganization/Runner_Dashboard"
