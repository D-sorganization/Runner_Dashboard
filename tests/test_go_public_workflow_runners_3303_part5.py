"""Go-public prep part 5: main-ci-auto-release on hosted runners (#3303)."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "main-ci-auto-release.yml"


def test_main_ci_auto_release_jobs_use_ubuntu_latest() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job_id in ("gate", "publish", "publish-dispatch"):
        assert data["jobs"][job_id]["runs-on"] == "ubuntu-latest"


def test_main_ci_auto_release_jobs_repeat_least_privilege_permissions() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job_id in ("gate", "publish", "publish-dispatch"):
        perms = data["jobs"][job_id].get("permissions") or {}
        assert perms.get("checks") == "read"
        assert perms.get("contents") == "write"
