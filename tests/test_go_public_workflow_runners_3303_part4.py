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
    text = (ROOT / ".github" / "workflows" / "local-only-runner-guard.yml").read_text(encoding="utf-8")
    assert '"verify-tag.yml"' in text
    assert '"util-queued-job-reaper.yml"' in text
