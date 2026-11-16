"""Go-public prep part 4: verify-tag and queued-job-reaper on hosted runners (#3303)."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
VERIFY_TAG = ROOT / ".github" / "workflows" / "verify-tag.yml"
REAPER = ROOT / ".github" / "workflows" / "util-queued-job-reaper.yml"


def _job_runs_on(path: Path, job_id: str) -> object:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data["jobs"][job_id]["runs-on"]


def test_verify_tag_job_uses_ubuntu_latest() -> None:
    assert _job_runs_on(VERIFY_TAG, "verify") == "ubuntu-latest"


def test_queued_job_reaper_uses_ubuntu_latest() -> None:
    assert _job_runs_on(REAPER, "reap") == "ubuntu-latest"


def test_verify_tag_job_has_least_privilege_permissions() -> None:
    data = yaml.safe_load(VERIFY_TAG.read_text(encoding="utf-8"))
    perms = data["jobs"]["verify"].get("permissions") or {}
    assert perms.get("contents") == "read"
    assert set(perms) <= {"contents"}


def test_go_public_part4_workflows_allowlisted_in_local_only_guard() -> None:
    from scripts import local_only_runner_guard as guard

    assert "verify-tag.yml" in guard._HOSTED_WORKFLOW_ALLOWLIST
    assert "util-queued-job-reaper.yml" in guard._HOSTED_WORKFLOW_ALLOWLIST


def test_reaper_job_has_explicit_actions_permissions() -> None:
    data = yaml.safe_load(REAPER.read_text(encoding="utf-8"))
    perms = data["jobs"]["reap"].get("permissions") or {}
    assert perms.get("contents") == "read"
    assert perms.get("actions") == "write"
    assert perms.get("pull-requests") == "read"
