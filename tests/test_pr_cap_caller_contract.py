"""Offline trust and distribution contracts for the warning-only PR-cap caller."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
BUNDLE = ROOT / ".github" / "fleet-pr-cap"
PR_SCOPE = "${{ github.repository }}-${{ github.event.pull_request.number }}"


def _workflow(name: str) -> dict[str, Any]:
    path = WORKFLOWS / name
    assert path.is_file(), f"Missing advisory workflow: {name}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_caller_uses_only_trusted_base_pr_metadata_events() -> None:
    caller = _workflow("pr-cap.yml")
    triggers = caller.get("on", caller.get(True))
    assert triggers == {
        "pull_request_target": {
            "branches": ["main"],
            "types": ["opened", "ready_for_review", "reopened", "labeled", "unlabeled"],
        }
    }
    assert caller["permissions"] == {}
    assert set(caller["jobs"]) == {"pr-cap"}
    job = caller["jobs"]["pr-cap"]
    assert job["uses"] == "./.github/workflows/reusable-pr-cap.yml"
    assert job["permissions"] == {"contents": "read", "pull-requests": "write"}
    assert "secrets" not in job
    assert "timeout-minutes" not in job
    assert "startsWith(github.event.label.name, 'agent:')" in job["if"]
    assert "github.event.label.name == 'ship-alone'" in job["if"]


@pytest.mark.parametrize(
    ("name", "prefix"),
    [("pr-cap.yml", "pr-cap-caller-"), ("reusable-pr-cap.yml", "pr-cap-reusable-")],
)
def test_advisory_serialization_is_pr_scoped_distinct_and_non_canceling(name: str, prefix: str) -> None:
    assert _workflow(name)["concurrency"] == {"group": prefix + PR_SCOPE, "cancel-in-progress": False}


def test_counter_executes_only_the_sparse_trusted_base_bundle() -> None:
    workflow = _workflow("reusable-pr-cap.yml")
    assert set(workflow.get("on", workflow.get(True))) == {"workflow_call"}
    assert workflow["permissions"] == {}
    assert set(workflow["jobs"]) == {"counter", "comment"}
    counter = workflow["jobs"]["counter"]
    assert counter["if"] == "github.event_name == 'pull_request_target'"
    assert counter["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert counter["timeout-minutes"] == 5
    steps = counter["steps"]
    checkout = [step for step in steps if step.get("uses", "").startswith("actions/checkout@")]
    assert len(checkout) == 1
    assert checkout[0]["with"] == {
        "ref": "${{ github.sha }}",
        "persist-credentials": False,
        "sparse-checkout": ".github/fleet-pr-cap",
    }
    report = next(step for step in steps if step.get("id") == "report")
    assert report["working-directory"] == ".github/fleet-pr-cap"
    assert report["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert report["env"]["TARGET_REPO"] == "${{ github.repository }}"
    assert report["env"]["TARGET_PR"] == "${{ github.event.pull_request.number }}"
    assert "python -m scripts.pr_cap_report" in report["run"]
    text = (WORKFLOWS / "reusable-pr-cap.yml").read_text(encoding="utf-8")
    for unsafe in ("pull_request.head", "head.ref", "head.sha", "secrets: inherit", "allow-unsafe-checkout"):
        assert unsafe not in text


def test_writer_is_separate_one_shot_and_does_not_modify_pr_state() -> None:
    writer = _workflow("reusable-pr-cap.yml")["jobs"]["comment"]
    assert writer["needs"] == "counter"
    assert writer["permissions"] == {"pull-requests": "write"}
    assert writer["timeout-minutes"] == 5
    assert writer["continue-on-error"] is True
    assert writer["concurrency"] == {"group": "pr-cap-comment-" + PR_SCOPE, "cancel-in-progress": False}
    assert "needs.counter.outputs.status == 'WARN'" in writer["if"]
    assert "cap_exceeded" in writer["if"] and "unknown_identity" in writer["if"]
    assert len(writer["steps"]) == 1
    step = writer["steps"][0]
    assert step["uses"].startswith("actions/github-script@")
    assert step["with"]["retries"] == 0
    script = step["with"]["script"]
    assert "${{" not in script
    assert "<!-- fleet-pr-cap -->" in script
    assert "github-actions[bot]" in script and "comments.some" in script
    assert "github.rest.issues.listComments" in script
    assert script.count("github.rest.issues.createComment") == 1
    for mutation in ("updateComment", "deleteComment", "pulls.update", "issues.update", "addLabels", "removeLabel"):
        assert mutation not in script


@pytest.mark.parametrize("name", ["pr-cap.yml", "reusable-pr-cap.yml"])
def test_non_canceling_exception_and_bulk_classification_are_explicit(name: str) -> None:
    policy = json.loads((ROOT / "config/workflow_concurrency_policy.json").read_text(encoding="utf-8"))
    reason = policy["cancel_in_progress_false_allowlist"][name]
    assert len(reason) >= 20
    assert name not in policy["pr_concurrency_singleton_allowlist"]
    routing = json.loads((ROOT / "config/workflow_runner_routing_policy.json").read_text(encoding="utf-8"))
    classes = [key for key, value in routing["workflow_classes"].items() if name in value["workflows"]]
    assert classes == ["bulk"]


def test_generated_manifest_checks_every_local_file_without_sibling_dependencies() -> None:
    manifest_path = BUNDLE / "MANIFEST.json"
    assert manifest_path.is_file(), "Missing generated local advisory bundle"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(manifest) == {"schema", "files"}
    assert manifest["schema"] == "fleet-pr-cap-bundle/v1"
    expected = {
        ".github/workflows/pr-cap.yml",
        ".github/workflows/reusable-pr-cap.yml",
        ".github/fleet-pr-cap/ruff.toml",
        ".github/fleet-pr-cap/scripts/__init__.py",
        ".github/fleet-pr-cap/scripts/pr_cap_check.py",
        ".github/fleet-pr-cap/scripts/pr_cap_report.py",
        ".github/fleet-pr-cap/scripts/pr_cap_preflight.py",
        ".github/fleet-pr-cap/shared_scripts/__init__.py",
        ".github/fleet-pr-cap/shared_scripts/agent_identity.py",
        ".github/fleet-pr-cap/shared_scripts/pr_agent_identity.py",
    }
    assert set(manifest["files"]) == expected
    for relative, digest in manifest["files"].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest


def test_bundle_imports_in_isolated_python_without_the_canonical_checkout() -> None:
    assert BUNDLE.is_dir(), "Missing generated local advisory bundle"
    script = (
        "import json,sys;from pathlib import Path;sys.path.insert(0,str(Path.cwd()));"
        "from scripts.pr_cap_check import PR_CAP;"
        "from shared_scripts.agent_identity import resolve_pr_agent;"
        "import scripts.pr_cap_report,scripts.pr_cap_preflight;"
        "print(json.dumps({'cap':PR_CAP,'identity':resolve_pr_agent({'headRefName':'codex/example'})}))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script], cwd=BUNDLE, capture_output=True, text=True, check=False, timeout=20
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"cap": 3, "identity": "codex"}


def test_manifest_baseline_audits_only_the_ten_verified_payload_digests() -> None:
    """Audited scanner fingerprints cover payload hashes, never arbitrary secrets."""
    manifest_path = BUNDLE / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    expected = {}
    for relative, digest in manifest["files"].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest
        # detect-secrets 1.5 fingerprints public file digests with SHA1, not authentication or secret material.
        # usedforsecurity=False preserves that upstream compatibility contract.
        hash_id = hashlib.sha1(  # nosemgrep: python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1
            digest.encode(), usedforsecurity=False
        ).hexdigest()
        expected[hash_id] = next(index for index, line in enumerate(lines, 1) if f'"{digest}"' in line)
    assert len(expected) == 10
    baseline = json.loads((ROOT / ".secrets.baseline").read_text(encoding="utf-8"))
    findings = baseline["results"][".github/fleet-pr-cap/MANIFEST.json"]
    assert len(findings) == len(expected)
    assert {item["hashed_secret"] for item in findings} == set(expected)
    for finding in findings:
        assert finding["type"] == "Hex High Entropy String"
        assert finding["is_secret"] is False
        assert type(finding["line_number"]) is int
        assert finding["line_number"] == expected[finding["hashed_secret"]]
