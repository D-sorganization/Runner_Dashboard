"""Offline workflow tier-routing audit tests for issue #757."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_workflow_runner_routing.py"
POLICY = REPO_ROOT / "config" / "workflow_runner_routing_policy.json"
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "runner-routing-labels.md"
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"


def _run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_repo_policy_references_real_workflows() -> None:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    workflow_names = {path.name for path in WORKFLOW_DIR.glob("*.yml")}

    for class_policy in policy["workflow_classes"].values():
        for workflow_name in class_policy["workflows"]:
            assert workflow_name in workflow_names


def test_repo_audit_is_green_but_reports_migration_recommendations() -> None:
    result = _run_script("--format", "json")
    assert result.returncode == 0, result.stderr or result.stdout

    payload = json.loads(result.stdout)
    assert payload["policy_errors"] == []
    assert payload["violations"] == []
    assert payload["recommendations"], "Expected neutral-label migration recommendations for current workflows"


def test_audit_flags_docker_workflow_pinned_only_to_bulk(tmp_path: Path) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    policy_path = tmp_path / "policy.json"

    _write_yaml(
        workflow_dir / "docker-build.yml",
        {
            "name": "docker-build",
            "on": {"pull_request": None},
            "jobs": {
                "docker-build-scan": {
                    "runs-on": "d-sorg-fleet-bulk",
                    "steps": [{"run": "echo hi"}],
                }
            },
        },
    )
    policy_path.write_text(
        json.dumps(
            {
                "neutral_labels": ["d-sorg-fleet"],
                "label_taxonomy": {
                    "d-sorg-fleet-bulk": {"purpose": "bulk"},
                    "d-sorg-fleet-docker": {"purpose": "docker"},
                    "d-sorg-fleet-nvme": {"purpose": "nvme"},
                },
                "workflow_classes": {
                    "docker": {
                        "description": "Docker jobs need fast disk",
                        "recommended_labels": ["d-sorg-fleet-docker", "d-sorg-fleet-nvme"],
                        "forbidden_labels": ["d-sorg-fleet-bulk"],
                        "workflows": ["docker-build.yml"],
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    result = _run_script(
        "--policy",
        str(policy_path),
        "--workflow-dir",
        str(workflow_dir),
        "--format",
        "json",
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["violations"]
    assert "forbidden tier label" in payload["violations"][0]["message"]


def test_audit_flags_lightweight_workflow_overusing_nvme(tmp_path: Path) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    policy_path = tmp_path / "policy.json"

    _write_yaml(
        workflow_dir / "labels-sync.yml",
        {
            "name": "labels-sync",
            "on": {"workflow_dispatch": None},
            "jobs": {
                "sync": {
                    "runs-on": "d-sorg-fleet-nvme",
                    "steps": [{"run": "echo hi"}],
                }
            },
        },
    )
    policy_path.write_text(
        json.dumps(
            {
                "neutral_labels": ["d-sorg-fleet"],
                "label_taxonomy": {
                    "d-sorg-fleet-bulk": {"purpose": "bulk"},
                    "d-sorg-fleet-fast-io": {"purpose": "fast"},
                    "d-sorg-fleet-nvme": {"purpose": "nvme"},
                },
                "workflow_classes": {
                    "bulk": {
                        "description": "Lightweight workflows should stay bulk",
                        "recommended_labels": ["d-sorg-fleet-bulk"],
                        "forbidden_labels": ["d-sorg-fleet-fast-io", "d-sorg-fleet-nvme"],
                        "workflows": ["labels-sync.yml"],
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    result = _run_script(
        "--policy",
        str(policy_path),
        "--workflow-dir",
        str(workflow_dir),
        "--format",
        "json",
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["violations"]
    assert "forbidden tier label" in payload["violations"][0]["message"]


def test_runbook_documents_taxonomy_and_audit_entrypoints() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")

    for label in (
        "d-sorg-fleet-bulk",
        "d-sorg-fleet-fast-io",
        "d-sorg-fleet-docker",
        "d-sorg-fleet-nvme",
    ):
        assert label in text

    assert "check_workflow_runner_routing.py" in text
    assert "workflow_runner_routing_policy.json" in text
    assert "neutral `d-sorg-fleet`" in text
